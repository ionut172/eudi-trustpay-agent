# Backend

FastAPI API and Google ADK orchestration for the agentic payment middleware prototype. The backend parses conversational intents, applies deterministic policy, coordinates mock EUDI verification, and guards the payment-adapter call.

> This is a development/demo backend only. The mock credentials, verifier results, recovery action, and payments are synthetic. The HTTPX `LIVE` adapters are integration stubs; they are not a production-ready EUDI or banking connection. The API also lacks customer authentication and session ownership enforcement.

## Run and test

From this directory:

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

Set `GEMINI_API_KEY` in the ignored local `.env` to enable conversational extraction. The Google GenAI SDK also reads `GOOGLE_API_KEY`; `app/config.py` bridges the configured Gemini key at runtime. Never commit secrets.

Run the backend tests with `uv run pytest`. They use mock components and make no live payment or verifier calls.

## Structure

- `app/main.py` — FastAPI routes for chat, payment intents, verification, execution, and development-only mock-wallet scenarios.
- `app/agents/` — ADK coordinator and intent, risk, EUDI, and execution specialist agents.
- `app/tools/payment_tools.py` — deterministic recipient resolution, authorization checks, verifier polling, and adapter execution.
- `app/domain/` — typed payment and credential models plus local mock-presentation validation.
- `app/adapters/base.py` — verifier/payment adapter interfaces.
- `app/adapters/mock_adapters.py` — synthetic verifier and core-banking implementations.
- `app/adapters/mock_wallet_api.py` and `mock_wallets.json` — local fixture wallets and recipients.
- `app/adapters/live_adapters.py` — illustrative HTTPX integration stubs; configure and security-review them before any live use.
- `tests/` — API, authorization-flow, agent-handoff, and validator regression tests.

## Authorization behavior

- LLM output is intent data only; it cannot authorize a transfer.
- New/changed recipients or payments outside trusted-recipient settings require step-up verification.
- The backend checks payment and verification state before calling the payment adapter.
- In this prototype, an exact server-configured trusted-recipient match can use a separate fast path; it is not EUDI verification.
- `MOCK_WALLET_AUTO_PRESENT=false` leaves wallet presentation as a manual demo action.
- `AUTO_EXECUTE_VERIFIED_PAYMENTS` controls the demo’s post-verdict execution behavior.

Do not treat mock status flags or generated mock receipt signatures as cryptographic proof. See the root [README](../README.md) and [live integration checklist](../integration.md) for scope and production gaps.
