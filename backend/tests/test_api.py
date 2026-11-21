"""Integration tests for FastAPI endpoints, ADK Agent, and Mock Wallet simulator."""

from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from app.domain.models import VerificationStatus
from app.main import app


@pytest.fixture(autouse=True)
def clean_mock_state():
    """Ensures a clean state file before each test run."""
    state_file = Path("/tmp/ing_mock_verifier_state.json")
    if state_file.exists():
        state_file.unlink()
    yield
    if state_file.exists():
        state_file.unlink()


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

    # User tries to execute payment without verification
    resp = await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "confirm payment and execute immediately",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "EXECUTION BLOCKED: Guardrail violation" in data["reply"]
    assert data["state"]["verification_status"] != VerificationStatus.VERIFIED.value


@pytest.mark.asyncio
async def test_preset_scenario_mismatch_blocks_fraud(client: AsyncClient):
    """Fraud scenario: Payee tries to spoof account -> SUBJECT_MISMATCH -> Payment blocked."""
    session_id = "test-e2e-fraud-mismatch"

    # 1. Customer initiates payment
    await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "Pay 150 EUR to Alice Visser at NL91INGB0001234567",
        },
    )

    # 2. Fraudulent wallet presents mismatched owner
    mismatch_resp = await client.post(f"/mock-wallet/{session_id}/preset/mismatch")
    assert mismatch_resp.status_code == 200
    assert mismatch_resp.json()["status"] == VerificationStatus.REJECTED.value
    assert mismatch_resp.json()["mode"] == "mock"
    assert mismatch_resp.json()["reason"] == "ACCOUNT_HOLDER_MISMATCH"

    # 3. Customer checks verification -> Warning emitted
    check_resp = await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "is payee verified?",
        },
    )
    assert check_resp.status_code == 200
    assert "SECURITY ALERT" in check_resp.json()["reply"]
    assert "SUBJECT MISMATCH" in check_resp.json()["reply"]
    assert "BLOCARE PERMANENTĂ A TRANZACȚIEI" in check_resp.json()["reply"]

    # 4. Attempt to force execution is blocked by guardrail
    exec_resp = await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "proceed with payment anyway",
        },
    )
    assert exec_resp.status_code == 200
    assert "EXECUTION BLOCKED: Guardrail violation" in exec_resp.json()["reply"]


@pytest.mark.asyncio
async def test_recovery_flow_missing_iban_to_verified(client: AsyncClient):
    """Recovery flow: missing_iban preset -> RECOVERY_REQUIRED -> recover-iban -> VERIFIED -> execute."""
    session_id = "test-e2e-recovery-flow"

    # 1. Initiate payment
    await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "Pay 200 euros to Alice Visser at NL91INGB0001234567",
        },
    )

    # 2. Wallet presents PID without IBAN attestation
    missing_resp = await client.post(f"/mock-wallet/{session_id}/preset/missing_iban")
    assert missing_resp.status_code == 200
    assert missing_resp.json()["status"] == VerificationStatus.RECOVERY_REQUIRED.value

    # 3. Agent informs user recovery is needed
    check_resp = await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "status of verification?",
        },
    )
    assert "RECOVERY REQUIRED" in check_resp.json()["reply"]
    assert "wallet-issuance" in check_resp.json()["reply"]

    # 4. Payee recovers/issues missing IBAN attestation from their bank
    recover_resp = await client.post(f"/mock-wallet/{session_id}/recover-iban")
    assert recover_resp.status_code == 200
    assert recover_resp.json()["status"] == VerificationStatus.VERIFIED.value

    # 5. Customer checks verification again -> now SUCCESSFUL
    check_resp2 = await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "user_id": "user-test",
            "message": "check status now",
        },
    )
    assert "Payee verification SUCCESSFUL" in check_resp2.json()["reply"]
    assert "Execuție automată autorizată" in check_resp2.json()["reply"]
    assert "executed successfully via SEPA Instant" in check_resp2.json()["reply"]
    assert check_resp2.json()["state"]["verification_status"] == VerificationStatus.COMPLETED.value
