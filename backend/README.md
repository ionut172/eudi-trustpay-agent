# ING EUDI Wallet Conversational Payments Backend

Production-ready backend service for conversational banking payments secured by **EUDI Wallet** (OID4VP / DCQL / OID4VCI) verification, built on **Google ADK (Agent Development Kit)**, **FastAPI**, and **Google Cloud Run**.

## Architecture Overview
- **Google ADK Orchestration**: Uses `PaymentOrchestratorAgent`, `Runner`, and `InMemorySessionService`.
- **Code-level Guardrail**: `execute_verified_instant_payment` strictly refuses payment execution unless `session.state["verification_status"] == "VERIFIED"`.
- **EUDI Wallet OID4VP Verification**: Evaluates PID cryptographic validity, IBAN ownership attestation, and Subject Binding (`pid.sub == iban.sub`).
- **Fraud Prevention**: Detects Authorised Push Payment (APP) fraud and IBAN spoofing.
- **OID4VCI Verifiable Payment Receipt**: Generates cryptographic `PaymentReceiptVC` upon successful payment execution.
- **Hexagonal Architecture**: Abstract ports (`IEUDIVerifierPort`, `ICoreBankingPort`) with Mock & Live HTTPX adapters.
- **Google Cloud Logging**: Structured JSON logs (`severity`, `message`, `component`, `session_id`, `request_id`, `event_type`).

## Quickstart

```bash
# Sync dependencies
uv sync

# Run tests
uv run pytest

# Run server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```
