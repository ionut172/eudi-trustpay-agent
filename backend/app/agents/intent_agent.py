"""Structured payment-intent extraction agent."""

from google.adk.agents import Agent
from app.config import Settings
from app.domain.models import PaymentIntent


INTENT_INSTRUCTION = """Extract a payment intent from the user's latest message.
Return only the structured fields in PaymentIntent.
Do not invent a recipient, IBAN, amount, or currency.
Do not make risk or authorization decisions.
The IBAN is optional in the user's message; the backend resolves it from configured payees.
Do not request or report a missing IBAN when the recipient and amount are known.
If required information is absent or ambiguous, explain what needs clarification.
"""


def create_intent_agent(settings: Settings) -> Agent:
    """Build the structured intent extractor; output_schema intentionally has no tools."""
    return Agent(
        name="payment_intent_agent",
        description="Extracts typed payment intent without inventing missing details",
        instruction=INTENT_INSTRUCTION,
        model=settings.MODEL_NAME,
        output_schema=PaymentIntent,
        output_key="payment_intent",
    )
