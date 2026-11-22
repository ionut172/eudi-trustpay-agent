"""Google ADK Agent orchestrator for EUDI Wallet conversational payments."""

import json
from typing import Any, Optional, Tuple
from pydantic import ValidationError
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types
from app.config import Settings, get_settings
from app.telemetry import get_logger
from app.domain.models import PaymentIntent
from app.agents.eudi_agent import create_eudi_agent
from app.agents.execution_agent import create_payment_execution_agent
from app.agents.intent_agent import create_intent_agent
from app.agents.risk_agent import create_risk_agent
from app.tools.payment_tools import (
    check_payment_verification,
    _execute_payment_for_state,
    initiate_payment_intent,
)

logger = get_logger("payment_orchestrator")

SYSTEM_INSTRUCTION = """You are a conversational payments assistant secured by the European Digital Identity (EUDI) Wallet.
Your primary objective is to execute bank payments while strictly preventing Authorised Push Payment (APP) fraud and IBAN spoofing.
Always communicate with the user in English, regardless of the language used in their message.

CRITICAL SECURITY RULES:
1. Risk Route: Use backend-configured trusted favorites only. New recipients, changed IBANs, and amounts above the configured limit require EUDI step-up.
2. EUDI Verification: Never accept conversational claims as verification. Call `check_payee_eudi_verification` for external status.
3. Fraud Prevention:
   - If verification returns SUBJECT_MISMATCH, immediately warn the user that the bank account holder does not match the intended payee. Under no circumstances execute the payment.
   - If it returns RECOVERY_REQUIRED, instruct the payee to obtain their missing IBAN attestation from their issuing bank.
4. Instant Payment Execution:
    - Execute only when backend authorization permits EUDI_VERIFIED or an exact trusted-favorite match below its configured limit.
    - Never describe a trusted-favorite fast path as EUDI or cryptographic verification.
   - Confirm payment execution and provide the transaction ID and Verifiable Payment Receipt (OID4VCI) details.
"""


class PaymentOrchestratorAgent:
    """Orchestrator wrapping the Google ADK Agent, Runner, and SessionService."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

        # Specialist definitions and prompts live in their own modules.
        intent_agent = create_intent_agent(self.settings)
        risk_agent = create_risk_agent(self.settings)
        trust_agent = create_eudi_agent(self.settings)
        execution_agent = create_payment_execution_agent(self.settings)

        self.agent = Agent(
            name="payment_orchestrator",
            description="Payment coordinator delegating intent, risk, EUDI trust, and execution stages",
            instruction=(
                SYSTEM_INSTRUCTION
                + " Delegate intent extraction to payment_intent_agent, risk decisions to payment_risk_agent, "
                + "EUDI verification to eudi_trust_agent, and authorized transfer to payment_execution_agent. "
                + "Never treat model output as authorization; backend tools enforce every decision."
            ),
            model=self.settings.MODEL_NAME,
            sub_agents=[intent_agent, risk_agent, trust_agent, execution_agent],
        )

        # In-memory session service (can later swap to DatabaseSessionService / Firestore)
        self.session_service = InMemorySessionService()

        # Google ADK Runner
        self.runner = Runner(
            app_name=self.settings.APP_NAME,
            agent=self.agent,
            session_service=self.session_service,
        )

        logger.info(
            "PaymentOrchestratorAgent initialized",
            model=self.settings.MODEL_NAME,
            adapter_mode=self.settings.ADAPTER_MODE,
        )

    async def get_or_create_session(self, user_id: str, session_id: str) -> Session:
        """Retrieves or initializes an ADK session with empty payment state."""
        session = await self.session_service.get_session(
            app_name=self.settings.APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        if not session:
            initial_state = {
                "session_id": session_id,
                "verification_status": "NOT_STARTED",
                "pending_payment": None,
                "verified_payee": None,
            }
            session = await self.session_service.create_session(
                app_name=self.settings.APP_NAME,
                user_id=user_id,
                session_id=session_id,
                state=initial_state,
            )
            logger.log_event(
                severity="INFO",
                message=f"Created new payment session {session_id} for user {user_id}",
                event_type="session_init",
                session_id=session_id,
            )
        return session

    async def process_message(
        self,
        session_id: str,
        user_id: str,
        message: str,
    ) -> Tuple[str, dict[str, Any]]:
        """Processes natural language through the ADK multi-agent coordinator."""
        session = await self.get_or_create_session(user_id=user_id, session_id=session_id)

        if not self.settings.GEMINI_API_KEY:
            return (
                "Conversational intent extraction is unavailable: configure GEMINI_API_KEY, "
                "or submit a structured payment intent to /api/payment-intents. No payment was started.",
                session.state,
            )

        try:
            # The output_key is session state; clear it so a greeting or an
            # incomplete new message can never reuse an earlier payment intent.
            self._clear_payment_intent(session)
            user_content = types.Content(
                parts=[types.Part.from_text(text=message)],
                role="user",
            )
            root_reply_parts: list[str] = []
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
            ):
                if (
                    getattr(event, "author", None) == self.agent.name
                    and event.content
                    and event.content.parts
                ):
                    root_reply_parts.extend(
                        part.text for part in event.content.parts if part.text
                    )

            refreshed_session = await self.session_service.get_session(
                app_name=self.settings.APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
            state = refreshed_session.state if refreshed_session else session.state
            raw_intent = state.pop("payment_intent", None)
            if refreshed_session:
                self._clear_payment_intent(refreshed_session)

            if raw_intent is not None:
                if isinstance(raw_intent, str):
                    raw_intent = json.loads(raw_intent)
                intent = PaymentIntent.model_validate(raw_intent)
                # IBAN lookup and missing-field prompts belong to the backend.
                # In particular, an LLM must not block configured favorite or
                # mock-wallet resolution by claiming that an IBAN is missing.
                if intent.recipient_name or intent.amount is not None:
                    return await self.process_intent(session_id, user_id, intent)
                if intent.clarification_needed:
                    return (
                        "Please provide the recipient and amount before I can start a payment.",
                        state,
                    )

            # Never surface specialist structured output as conversational text.
            reply = "".join(root_reply_parts)
            if reply.lstrip().startswith("{"):
                try:
                    json.loads(reply)
                    reply = ""
                except json.JSONDecodeError:
                    pass
            return reply or "Hello! How can I help?", state
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning(f"ADK returned an invalid payment intent: {exc}", session_id=session_id)
            return "I couldn't understand the request. Please provide the recipient and amount. No payment has been started.", session.state
        except Exception as exc:
            logger.error(f"ADK multi-agent request failed: {exc}", session_id=session_id)
            return "Payment assistant unavailable. No payment was started.", session.state

    async def process_intent(
        self,
        session_id: str,
        user_id: str,
        intent: PaymentIntent,
    ) -> Tuple[str, dict[str, Any]]:
        """Process a validated structured intent without a mock ToolContext."""
        session = await self.get_or_create_session(user_id=user_id, session_id=session_id)
        reply = await initiate_payment_intent(
            intent=intent,
            session_id=session_id,
            state=session.state,
            settings=self.settings,
        )
        self._sync_storage_state(session)
        return reply, session.state

    async def check_verification(
        self,
        session_id: str,
        user_id: str,
    ) -> Tuple[str, dict[str, Any]]:
        """Poll EUDI verifier and optionally execute a fully verified payment."""
        session = await self.get_or_create_session(user_id=user_id, session_id=session_id)
        reply = await check_payment_verification(session_id, session.state, self.settings)
        self._sync_storage_state(session)
        return reply, session.state

    async def execute_payment(
        self,
        session_id: str,
        user_id: str,
    ) -> Tuple[str, dict[str, Any]]:
        """Run backend execution guardrail for the current payment session."""
        session = await self.get_or_create_session(user_id=user_id, session_id=session_id)
        reply = await _execute_payment_for_state(session_id, session.state, self.settings)
        self._sync_storage_state(session)
        return reply, session.state

    def _sync_storage_state(self, session: Session) -> None:
        """Syncs the internal session storage state dictionary."""
        app_name = self.settings.APP_NAME
        user_id = session.user_id
        session_id = session.id
        if (
            app_name in self.session_service.sessions
            and user_id in self.session_service.sessions[app_name]
            and session_id in self.session_service.sessions[app_name][user_id]
        ):
            self.session_service.sessions[app_name][user_id][session_id].state.update(session.state)

    def _clear_payment_intent(self, session: Session) -> None:
        """Remove internal structured output from both session state copies."""
        session.state.pop("payment_intent", None)
        stored_session = (
            self.session_service.sessions
            .get(self.settings.APP_NAME, {})
            .get(session.user_id, {})
            .get(session.id)
        )
        if stored_session:
            stored_session.state.pop("payment_intent", None)


_global_orchestrator: Optional[PaymentOrchestratorAgent] = None


def get_orchestrator(settings: Optional[Settings] = None) -> PaymentOrchestratorAgent:
    """Returns singleton instance of PaymentOrchestratorAgent."""
    global _global_orchestrator
    if _global_orchestrator is None:
        _global_orchestrator = PaymentOrchestratorAgent(settings=settings)
    return _global_orchestrator
