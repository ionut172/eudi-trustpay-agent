"""Integration tests for FastAPI endpoints, ADK Agent, and Mock Wallet simulator."""

from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from app.adapters import mock_adapters
from app.adapters.mock_adapters import MockCoreBankingAdapter, MockEUDIVerifierAdapter
from app.domain.models import (
    PaymentAuthorization,
    PaymentExecutionRequest,
    VerificationStatus,
)
from app.main import app, settings
from app.agents.orchestrator import get_orchestrator


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
async def test_replayed_presentation_nonce_cannot_verify_new_request(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_adapters, "STATE_FILE", tmp_path / "mock-state.json")
    verifier = MockEUDIVerifierAdapter()
    req_a = await verifier.create_dcql_request(
        session_id="nonce-replay-test",
        target_iban="RO23INGB999900001231",
        expected_payee_name="George Visser",
        amount=150,
    )
    old_presentation = await verifier._wallet_api.fetch_presentation(
        session_id="nonce-replay-test",
        expected_name="George Visser",
        target_iban="RO23INGB999900001231",
    )
    old_presentation.nonce = req_a.nonce
    req_b = await verifier.create_dcql_request(
        session_id="nonce-replay-test",
        target_iban="RO23INGB999900001231",
        expected_payee_name="George Visser",
        amount=150,
    )

    stale_result = await verifier.submit_wallet_presentation(
        "nonce-replay-test", old_presentation
    )

    assert req_b.request_id != req_a.request_id
    assert stale_result.status == VerificationStatus.TRUST_ERROR
    assert await verifier.get_verification_result("nonce-replay-test") is None


def test_adapter_disk_sync_drops_sessions_removed_from_snapshot(monkeypatch):
    verifier = MockEUDIVerifierAdapter()
    banking = MockCoreBankingAdapter()
    verifier._requests["removed"] = None
    verifier._presentations["removed"] = None
    verifier._results["removed"] = None
    banking._receipts["removed"] = []
    monkeypatch.setattr(mock_adapters, "_read_persistent_state", lambda: {})

    verifier._sync_from_disk()
    banking._sync_from_disk()

    assert "removed" not in verifier._requests
    assert "removed" not in verifier._presentations
    assert "removed" not in verifier._results
    assert "removed" not in banking._receipts


@pytest.mark.asyncio
async def test_payment_adapter_idempotency_requires_identical_details():
    banking = MockCoreBankingAdapter()
    request = PaymentExecutionRequest(
        session_id="idempotency-test",
        payment_id="payment-stable-id",
        debtor_account="DE89370400440532013000",
        creditor_iban="DE89370400440532013001",
        creditor_name="Alice Example",
        amount=25,
        currency="EUR",
    )
    authorization = PaymentAuthorization(
        decision="TRUSTED_FAVORITE",
        session_id=request.session_id,
        payment_id=request.payment_id,
        recipient_name=request.creditor_name,
        recipient_iban=request.creditor_iban,
        amount=request.amount,
        currency=request.currency,
        favorite_name=request.creditor_name,
        max_amount=100,
        reason="Test authorization",
    )

    first_receipt = await banking.execute_instant_payment(request, authorization)
    retry_receipt = await banking.execute_instant_payment(request, authorization)
    assert retry_receipt.transaction_id == first_receipt.transaction_id
    assert len(await banking.get_receipts(request.session_id)) == 1

    changed_request = request.model_copy(update={"currency": "USD"})
    changed_authorization = authorization.model_copy(update={"currency": "USD"})
    with pytest.raises(ValueError, match="payment id was reused with different details"):
        await banking.execute_instant_payment(changed_request, changed_authorization)


@pytest.mark.asyncio
async def test_verification_request_currency_must_match_payment(client: AsyncClient):
    session_id = "currency-binding-test"
    settings.AUTO_EXECUTE_VERIFIED_PAYMENTS = False
    await client.post(
        "/api/payment-intents",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "intent": {
                "recipient_name": "George Visser",
                "recipient_iban": "RO23INGB999900001231",
                "amount": 150,
                "currency": "EUR",
            },
        },
    )
    verified = await client.post(f"/mock-wallet/{session_id}/preset/valid")
    assert verified.json()["status"] == VerificationStatus.VERIFIED.value
    orchestrator = get_orchestrator(settings)
    session = await orchestrator.get_or_create_session("user-test", session_id)
    session.state["pending_payment"]["currency"] = "USD"
    session.state["verification_status"] = VerificationStatus.VERIFIED.value
    orchestrator._sync_storage_state(session)

    response = await client.post(f"/api/payments/{session_id}/execute?user_id=user-test")

    assert "Verification request does not match pending payment" in response.json()["reply"]
    assert response.json()["state"]["verification_status"] == VerificationStatus.VERIFIED.value


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

    # Re-polling or explicitly executing a completed payment must not create another transfer.
    repeated_check = await client.post(
        f"/api/payments/{session_id}/verification?user_id=user-test"
    )
    repeated_execute = await client.post(
        f"/api/payments/{session_id}/execute?user_id=user-test"
    )
    receipts = await client.get(f"/mock-wallet/{session_id}/receipts")
    assert "already completed" in repeated_check.json()["reply"].lower()
    assert "already completed" in repeated_execute.json()["reply"].lower()
    assert len(receipts.json()) == 1


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
