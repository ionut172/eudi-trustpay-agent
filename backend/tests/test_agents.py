"""Architecture tests for specialist ADK agent definitions."""

import os
from types import SimpleNamespace
import pytest
import app.agents.orchestrator as orchestrator_module
from app.agents.orchestrator import PaymentOrchestratorAgent
from app.agents.eudi_agent import create_eudi_agent
from app.agents.execution_agent import create_payment_execution_agent
from app.agents.intent_agent import create_intent_agent
from app.agents.risk_agent import create_risk_agent
from app.config import Settings, get_settings
from app.domain.models import PaymentIntent


class _IntentRunner:
    def __init__(self, orchestrator, intent):
        self.orchestrator = orchestrator
        self.intent = intent

    async def run_async(self, user_id, session_id, new_message):
        session = await self.orchestrator.session_service.get_session(
            app_name=self.orchestrator.settings.APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        if self.intent is not None:
            session.state["payment_intent"] = self.intent
            self.orchestrator._sync_storage_state(session)
        yield SimpleNamespace(author="payment_intent_agent", content=None)


def test_gemini_key_from_settings_reaches_genai_sdk(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.GEMINI_API_KEY == "offline-test-key"
    assert os.environ["GOOGLE_API_KEY"] == "offline-test-key"
    get_settings.cache_clear()


def test_specialist_agents_have_separate_roles_and_instructions():
    settings = Settings(GEMINI_API_KEY="", MODEL_NAME="gemini-2.5-flash")
    agents = [
        create_intent_agent(settings),
        create_risk_agent(settings),
        create_eudi_agent(settings),
        create_payment_execution_agent(settings),
    ]

    assert [agent.name for agent in agents] == [
        "payment_intent_agent",
        "payment_risk_agent",
        "eudi_trust_agent",
        "payment_execution_agent",
    ]
    assert all(agent.instruction.strip() for agent in agents)
    assert agents[0].output_schema is PaymentIntent
    assert agents[0].tools == []


@pytest.mark.asyncio
async def test_chat_hands_structured_intent_to_payment_flow(monkeypatch):
    settings = Settings(GEMINI_API_KEY="offline-test-key", MODEL_NAME="gemini-2.5-flash")
    orchestrator = PaymentOrchestratorAgent(settings)
    orchestrator.runner = _IntentRunner(
        orchestrator,
        {
            "recipient_name": "Alice Visser",
            "amount": 42,
            "currency": "EUR",
            "clarification_needed": "Missing IBAN for the recipient Alice Visser.",
        },
    )
    processed = []

    async def fake_initiate(intent, session_id, state, settings):
        processed.append((intent, session_id))
        state["pending_payment"] = {"amount": intent.amount}
        return "EUDI verification has been started."

    monkeypatch.setattr(orchestrator_module, "initiate_payment_intent", fake_initiate)

    reply, state = await orchestrator.process_message("handoff-test", "user-test", "Pay Alice 42 EUR")

    assert reply == "EUDI verification has been started."
    assert len(processed) == 1
    assert processed[0][0] == PaymentIntent(
        recipient_name="Alice Visser",
        amount=42,
        currency="EUR",
        clarification_needed="Missing IBAN for the recipient Alice Visser.",
    )
    assert "payment_intent" not in state
    assert state["pending_payment"]["amount"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "amount", "resolved_name", "iban"),
    [
        ("Ionut", 500, "Ionut Gabriel", "RO23INGB999900001231"),
        ("George Visser", 125, "George Visser", "RO23INGB999900001231"),
        ("Alice Visser", 75.5, "Alice Visser", "NL91INGB0001234567"),
    ],
)
async def test_chat_resolves_mock_wallet_iban_despite_model_clarification(
    name, amount, resolved_name, iban
):
    settings = Settings(
        GEMINI_API_KEY="offline-test-key",
        MODEL_NAME="gemini-2.5-flash",
        ADAPTER_MODE="MOCK",
        MOCK_WALLET_AUTO_PRESENT=False,
        ALLOW_MOCK_VERIFICATION=True,
    )
    orchestrator = PaymentOrchestratorAgent(settings)
    orchestrator.runner = _IntentRunner(
        orchestrator,
        {
            "recipient_name": name,
            "amount": amount,
            "currency": "EUR",
            "clarification_needed": f"Missing IBAN for the recipient {name}.",
        },
    )

    reply, state = await orchestrator.process_message(
        f"wallet-resolution-{name.split()[0]}",
        "user-test",
        f"Send €{amount} to {name}",
    )

    assert "Missing IBAN" not in reply
    assert "EUDI verification has been started" in reply
    assert state["pending_payment"]["creditor_name"] == resolved_name
    assert state["pending_payment"]["creditor_iban"] == iban
    assert state["verification_status"] == "PENDING"
    assert state.get("verified_payee") is None


@pytest.mark.asyncio
async def test_chat_does_not_reuse_stale_intent_for_greeting(monkeypatch):
    settings = Settings(GEMINI_API_KEY="offline-test-key", MODEL_NAME="gemini-2.5-flash")
    orchestrator = PaymentOrchestratorAgent(settings)
    session = await orchestrator.get_or_create_session("user-test", "greeting-test")
    session.state["payment_intent"] = {"recipient_name": "Alice Visser", "amount": 42}
    orchestrator._sync_storage_state(session)
    orchestrator.runner = _IntentRunner(
        orchestrator,
        {"recipient_name": None, "recipient_iban": None, "amount": None},
    )

    async def fail_if_called(*args, **kwargs):
        pytest.fail("A greeting must not start a payment")

    monkeypatch.setattr(orchestrator_module, "initiate_payment_intent", fail_if_called)

    reply, state = await orchestrator.process_message("greeting-test", "user-test", "Salut")

    assert reply == "Hello! How can I help?"
    assert "payment_intent" not in state
    assert state["verification_status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_chat_with_missing_amount_only_asks_for_clarification():
    settings = Settings(GEMINI_API_KEY="offline-test-key", MODEL_NAME="gemini-2.5-flash")
    orchestrator = PaymentOrchestratorAgent(settings)
    orchestrator.runner = _IntentRunner(
        orchestrator,
        {"recipient_name": "Alice Visser", "amount": None},
    )

    reply, state = await orchestrator.process_message(
        "incomplete-test", "user-test", "Pay Alice Visser"
    )

    assert reply == "What amount would you like to send to **Alice Visser**?"
    assert state["verification_status"] == "NOT_STARTED"
    assert state["pending_payment"] is None
    assert "payment_intent" not in state
