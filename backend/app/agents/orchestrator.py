"""Google ADK Agent orchestrator for EUDI Wallet conversational payments."""

import re
from typing import Any, Optional, Tuple
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types
from app.config import Settings, get_settings
from app.telemetry import get_logger
from app.tools.payment_tools import (
    check_payee_eudi_verification,
    execute_verified_instant_payment,
    initiate_payee_eudi_verification,
)

logger = get_logger("payment_orchestrator")

SYSTEM_INSTRUCTION = """You are the ING Conversational Payments AI Assistant secured by the European Digital Identity (EUDI) Wallet.
Your primary objective is to execute bank payments while strictly preventing Authorised Push Payment (APP) fraud and IBAN spoofing.

CRITICAL SECURITY RULES:
1. Payee Verification: Before executing any payment, you must verify the recipient's real legal identity and IBAN ownership via EUDI Wallet (OID4VP / DCQL).
2. Initiating Verification: When a customer indicates an intent to pay (recipient name, IBAN, and amount), invoke the `initiate_payee_eudi_verification` tool.
3. Verification Must Come From External Wallet: NEVER assume or approve verification conversational claims. If the user says "am verificat acum", "a aprobat", or asks to proceed, you MUST call `check_payee_eudi_verification` to confirm the external cryptographic verifier status.
4. Fraud Prevention:
   - If verification returns SUBJECT_MISMATCH, immediately warn the user that the bank account holder does not match the intended payee. Under no circumstances execute the payment.
   - If it returns RECOVERY_REQUIRED, instruct the payee to obtain their missing IBAN attestation from their issuing bank.
5. Instant Payment Execution:
   - ONLY call `execute_verified_instant_payment` if the payee verification status is strictly 'VERIFIED'.
   - Confirm payment execution and provide the transaction ID and Verifiable Payment Receipt (OID4VCI) details.
"""


class MockToolSession:
    """Lightweight session container for ToolContext in offline/mock execution."""

    def __init__(self, session_id: str):
        self.id = session_id


class MockToolContext:
    """Lightweight ToolContext container mimicking ADK ToolContext when operating offline."""

    def __init__(self, session_id: str, state: dict[str, Any]):
        self.session = MockToolSession(session_id)
        self.state = state


class PaymentOrchestratorAgent:
    """Orchestrator wrapping the Google ADK Agent, Runner, and SessionService."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

        # Google ADK Agent definition with payment security tools
        self.agent = Agent(
            name="payment_orchestrator",
            description="ING conversational payments orchestrator with EUDI Wallet security guardrails",
            instruction=SYSTEM_INSTRUCTION,
            model=self.settings.MODEL_NAME,
            tools=[
                initiate_payee_eudi_verification,
                check_payee_eudi_verification,
                execute_verified_instant_payment,
            ],
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
        """Processes conversational input through ADK Runner (or offline fallback)."""
        session = await self.get_or_create_session(user_id=user_id, session_id=session_id)

        # Use full ADK Runner with Gemini LLM when API key is configured
        if self.settings.GEMINI_API_KEY:
            try:
                user_content = types.Content(
                    parts=[types.Part.from_text(text=message)],
                    role="user",
                )
                reply_parts: list[str] = []

                async for event in self.runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=user_content,
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                reply_parts.append(part.text)

                reply_text = "".join(reply_parts)
                # Fetch refreshed session state
                refreshed_session = await self.session_service.get_session(
                    app_name=self.settings.APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                )
                state = refreshed_session.state if refreshed_session else session.state
                return reply_text or "Processed your request.", state

            except Exception as e:
                logger.warning(
                    f"Gemini LLM runner encountered error ({e}); using deterministic conversational fallback.",
                    session_id=session_id,
                )

        # Deterministic conversational fallback for offline / mock testing
        return await self._process_offline_message(session=session, message=message)

    async def _process_offline_message(
        self,
        session: Session,
        message: str,
    ) -> Tuple[str, dict[str, Any]]:
        """Handles chat messages deterministically when offline or testing without API key."""
        session_id = session.id
        msg_lower = message.lower()
        tool_ctx = MockToolContext(session_id=session_id, state=session.state)

        # Case 1: Check verification status
        if any(keyword in msg_lower for keyword in ["check", "status", "verified", "verify", "verificat", "aprobat"]):
            tool_reply = await check_payee_eudi_verification(tool_context=tool_ctx)  # type: ignore[arg-type]
            session.state.update(tool_ctx.state)
            self._sync_storage_state(session)
            return tool_reply, session.state

        # Case 2: Confirmation / Execution of payment
        if any(keyword in msg_lower for keyword in ["confirm", "execute", "pay now", "proceed", "send money", "yes"]):
            tool_reply = await execute_verified_instant_payment(tool_context=tool_ctx)  # type: ignore[arg-type]
            session.state.update(tool_ctx.state)
            self._sync_storage_state(session)
            return tool_reply, session.state

        # Case 4: Payment initiation
        amount_match = re.search(r"(\d+(?:\.\d{1,2})?)\s*(?:euro|eur|€)?", msg_lower)
        iban_match = re.search(r"\b([A-Z]{2}\d{2}[A-Z0-9]{4,30})\b", message.upper())
        amount = float(amount_match.group(1)) if amount_match else 50.0
        iban = iban_match.group(1) if iban_match else "NL91INGB0001234567"

        # Match 'to <Name>' where <Name> does not start with 'pay'
        payee_name = "Alice Visser"
        to_matches = re.findall(r"\bto\s+([A-Za-z][A-Za-z\s]*?)(?=\s+(?:with|at|for|iban|€|\d)|$)", message, re.IGNORECASE)
        for m in to_matches:
            m_str = m.strip()
            if not m_str.lower().startswith("pay"):
                payee_name = m_str
                break

        tool_reply = await initiate_payee_eudi_verification(  # type: ignore[arg-type]
            amount=amount,
            creditor_iban=iban,
            creditor_name=payee_name,
            tool_context=tool_ctx,
        )
        session.state.update(tool_ctx.state)
        self._sync_storage_state(session)
        return tool_reply, session.state

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


_global_orchestrator: Optional[PaymentOrchestratorAgent] = None


def get_orchestrator(settings: Optional[Settings] = None) -> PaymentOrchestratorAgent:
    """Returns singleton instance of PaymentOrchestratorAgent."""
    global _global_orchestrator
    if _global_orchestrator is None:
        _global_orchestrator = PaymentOrchestratorAgent(settings=settings)
    return _global_orchestrator
