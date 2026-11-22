"""Integration tests for FastAPI endpoints, ADK Agent, and Mock Wallet simulator."""

from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from app.domain.models import VerificationStatus
from app.main import app, settings


@pytest.fixture(autouse=True)
def clean_mock_state():
    """Ensures a clean state file before each test run."""
    state_file = Path("/tmp/ing_mock_verifier_state.json")
    if state_file.exists():
        state_file.unlink()
    yield
    if state_file.exists():
        state_file.unlink()


@pytest.fixture(autouse=True)
def configure_test_policy():
    settings.GEMINI_API_KEY = ""
    settings.MOCK_WALLET_AUTO_PRESENT = False
    settings.AUTO_EXECUTE_VERIFIED_PAYMENTS = True
    settings.TRUSTED_FAVORITES = {}
    settings.FAVORITE_FAST_PATH_MAX_AMOUNT = 500.0
    yield


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_probes(client: AsyncClient):
    """Verifies Cloud Run health and readiness probes."""
    resp_health = await client.get("/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "ok"
    assert "adapter_mode" in resp_health.json()

    resp_ready = await client.get("/readyz")
    assert resp_ready.status_code == 200
    assert resp_ready.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_guardrail_prevents_unverified_payment(client: AsyncClient):
    """Verifies that payment execution is blocked when verification is not VERIFIED."""
    session_id = "test-guardrail-session"

    await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {
                "recipient_name": "Alice Visser",
                "recipient_iban": "NL91INGB0001234567",
                "amount": 150,
            },
        },
    )
    resp = await client.post(f"/api/payments/{session_id}/execute?user_id=user-test")
    assert resp.status_code == 200
    data = resp.json()
    assert "EXECUTION BLOCKED: Guardrail violation" in data["reply"]
    assert data["state"]["verification_status"] != VerificationStatus.VERIFIED.value


@pytest.mark.asyncio
async def test_preset_scenario_mismatch_blocks_fraud(client: AsyncClient):
    """Fraud scenario: Payee tries to spoof account -> SUBJECT_MISMATCH -> Payment blocked."""
    session_id = "test-e2e-fraud-mismatch"

    # 1. Customer initiates payment
    init_resp = await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {
                "recipient_name": "Alice Visser",
                "recipient_iban": "NL91INGB0001234567",
                "amount": 150,
            },
        },
    )
    assert init_resp.status_code == 200

    # 2. Fraudulent wallet presents mismatched owner
    mismatch_resp = await client.post(f"/mock-wallet/{session_id}/preset/mismatch")
    assert mismatch_resp.status_code == 200
    assert mismatch_resp.json()["status"] == VerificationStatus.REJECTED.value
    assert mismatch_resp.json()["mode"] == "mock"
    assert mismatch_resp.json()["reason"] == "ACCOUNT_HOLDER_MISMATCH"

    # 3. Customer checks verification -> Warning emitted
    check_resp = await client.post(
        f"/api/payments/{session_id}/verification?user_id=user-test"
    )
    assert check_resp.status_code == 200
    assert "SECURITY ALERT" in check_resp.json()["reply"]
    assert "SUBJECT MISMATCH" in check_resp.json()["reply"]
    assert "PERMANENT PAYMENT BLOCK" in check_resp.json()["reply"]

    # 4. Attempt to force execution is blocked by guardrail
    exec_resp = await client.post(f"/api/payments/{session_id}/execute?user_id=user-test")
    assert exec_resp.status_code == 200
    assert "EXECUTION BLOCKED: Guardrail violation" in exec_resp.json()["reply"]


@pytest.mark.asyncio
async def test_new_payment_in_same_session_does_not_reuse_old_verification(client: AsyncClient):
    session_id = "test-fresh-verification-per-payment"
    first_payment = {
        "session_id": session_id,
        "user_id": "user-test",
        "intent": {
            "recipient_name": "George Visser",
            "recipient_iban": "RO23INGB999900001231",
            "amount": 150,
        },
    }

    await client.post("/api/payment-intents", json=first_payment)
    verified = await client.post(f"/mock-wallet/{session_id}/preset/valid")
    assert verified.json()["status"] == VerificationStatus.VERIFIED.value

    second_payment = {**first_payment, "intent": {**first_payment["intent"], "amount": 175}}
    await client.post("/api/payment-intents", json=second_payment)
    result = await client.post(
        f"/api/payments/{session_id}/verification?user_id=user-test"
    )

    assert result.status_code == 200
    assert result.json()["state"]["verification_status"] == VerificationStatus.PENDING.value
    assert "still pending" in result.json()["reply"].lower()


@pytest.mark.asyncio
async def test_recovery_flow_missing_iban_to_verified(client: AsyncClient):
    """Recovery flow: missing_iban preset -> RECOVERY_REQUIRED -> recover-iban -> VERIFIED -> execute."""
    session_id = "test-e2e-recovery-flow"

    # 1. Initiate payment
    init_resp = await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {
                "recipient_name": "Alice Visser",
                "recipient_iban": "NL91INGB0001234567",
                "amount": 200,
            },
        },
    )
    assert init_resp.status_code == 200

    # 2. Wallet presents PID without IBAN attestation
    missing_resp = await client.post(f"/mock-wallet/{session_id}/preset/missing_iban")
    assert missing_resp.status_code == 200
    assert missing_resp.json()["status"] == VerificationStatus.RECOVERY_REQUIRED.value

    # 3. Agent informs user recovery is needed
    check_resp = await client.post(
        f"/api/payments/{session_id}/verification?user_id=user-test"
    )
    assert "Verification is incomplete" in check_resp.json()["reply"]
    assert "valid EUDI PID" in check_resp.json()["reply"]
    assert "This demo simulates the bank issuer's recovery step" in check_resp.json()["reply"]

    # 4. Payee recovers/issues missing IBAN attestation from their bank
    recover_resp = await client.post(f"/mock-wallet/{session_id}/recover-iban")
    assert recover_resp.status_code == 200
    assert recover_resp.json()["status"] == VerificationStatus.VERIFIED.value

    # 5. Customer checks verification again -> now SUCCESSFUL
    check_resp2 = await client.post(
        f"/api/payments/{session_id}/verification?user_id=user-test"
    )
    assert "Payee verification SUCCESSFUL" in check_resp2.json()["reply"]
    assert "Automatic execution authorized" in check_resp2.json()["reply"]
    assert "executed successfully via SEPA Instant" in check_resp2.json()["reply"]
    assert check_resp2.json()["state"]["verification_status"] == VerificationStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_trusted_favorite_fast_path_requires_exact_server_record(client: AsyncClient):
    session_id = "test-favorite-fast-path"
    settings.TRUSTED_FAVORITES = {"Alice Visser": "NL91INGB0001234567"}

    resp = await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {"recipient_name": "Alice Visser", "amount": 100},
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "executed successfully via SEPA Instant" in data["reply"]
    assert data["state"]["verification_status"] == VerificationStatus.COMPLETED.value
    assert data["state"]["payment_authorization"]["decision"] == "TRUSTED_FAVORITE"


@pytest.mark.asyncio
async def test_changed_favorite_iban_requires_eudi_step_up(client: AsyncClient):
    session_id = "test-favorite-iban-change"
    settings.TRUSTED_FAVORITES = {"Alice Visser": "NL91INGB0001234567"}

    resp = await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {
                "recipient_name": "Alice Visser",
                "recipient_iban": "NL91INGB0007654321",
                "amount": 100,
            },
        },
    )

    assert resp.status_code == 200
    assert resp.json()["state"]["verification_status"] == VerificationStatus.PENDING.value


@pytest.mark.asyncio
async def test_favorite_above_amount_limit_requires_eudi_step_up(client: AsyncClient):
    session_id = "test-favorite-amount-limit"
    settings.TRUSTED_FAVORITES = {"Alice Visser": "NL91INGB0001234567"}

    resp = await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {"recipient_name": "Alice Visser", "amount": 500.01},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["state"]["verification_status"] == VerificationStatus.PENDING.value


@pytest.mark.asyncio
async def test_chat_without_llm_does_not_parse_or_start_payment(client: AsyncClient):
    resp = await client.post(
        "/api/chat",
        json={
            "session_id": "test-no-llm-chat",
            "user_id": "user-test",
            "message": "Pay 100 EUR to Alice Visser",
        },
    )

    assert resp.status_code == 200
    assert "configure GEMINI_API_KEY" in resp.json()["reply"]
    assert resp.json()["state"]["verification_status"] == VerificationStatus.NOT_STARTED.value
