# ING EUDI TrustPay Agent — Backend

FastAPI and Google ADK backend for conversational instant payments secured by EUDI Wallet verification.

## Development flow

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
uv run pytest
```

Copy `.env.example` to `.env` for local configuration. Never commit `.env` or real credentials.

## Repository structure

### `app/`

The application package contains the HTTP API, agent orchestration, domain rules and external-system adapters.

- `app/main.py` — FastAPI application, route registration, health endpoints, chat endpoint and mock wallet endpoints.
- `app/config.py` — environment configuration and adapter mode settings.
- `app/telemetry.py` — structured application logging setup.

### `app/agents/`

Google ADK orchestration.

- `app/agents/orchestrator.py` — creates the payment agent, manages sessions and chooses automatic versus fallback conversation handling.

### `app/tools/`

ADK tools used by the payment agent.

- `app/tools/payment_tools.py` — starts verification, checks wallet verification and executes instant payments behind code-level guardrails.

### `app/domain/`

Framework-independent payment and verification models.

- `app/domain/models.py` — Pydantic request, credential, verification and receipt models.
- `app/domain/validators.py` — pure wallet presentation validation rules.

### `app/adapters/`

Hexagonal adapter boundary for verifier and banking integrations.

- `app/adapters/base.py` — interfaces implemented by mock and live adapters.
- `app/adapters/mock_adapters.py` — persistent local verifier and core-banking simulation.
- `app/adapters/mock_wallet_api.py` — dedicated synthetic EU Wallet response client used by mock scenarios.
- `app/adapters/live_adapters.py` — HTTPX implementation for external live verifier and core-banking APIs.

### `tests/`

FastAPI integration and domain validation tests.

- `tests/test_api.py` — API and end-to-end conversation scenarios.
- `tests/test_validators.py` — credential trust, subject binding and IBAN matching rules.

### Other backend files

- `pyproject.toml` — Python dependencies and pytest configuration.
- `uv.lock` — reproducible dependency versions.
- `postman_collection.json` — manually usable API scenarios.
- `Dockerfile` — container build definition.
- `.env.example` — safe configuration template; `.env` is ignored by Git.

## Payment authorization flow

1. The chat handler receives a payment intent.
2. `payment_tools.initiate_payee_eudi_verification` creates a pending OID4VP request.
3. In mock mode, `mock_wallet_api.py` supplies a synthetic wallet presentation.
4. `domain/validators.py` evaluates identity, credential validity and IBAN ownership binding.
5. Only a result with status `VERIFIED` may reach the instant-payment guardrail.
6. `payment_tools.execute_verified_instant_payment` verifies the status again before touching the core-banking adapter.
7. A successful mock payment generates a signed mock `PaymentReceiptVC` credential.

## Key guardrail

A live or mock agent must never rely on conversational text to approve a payment. The execution tool retrieves the verification result and refuses everything except `VERIFIED`. Mock mode is unsuitable for live or production execution.

## Mock scenarios

The mock adapter supports:

- `valid` — valid PID and matching IBAN attestation;
- `mismatch` — subject/account-holder mismatch;
- `iban_mismatch` — same identity but a different wallet IBAN;
- `missing_iban` — PID is present but IBAN attestation is missing.

Preset routes are test facilities and should be disabled outside development.
