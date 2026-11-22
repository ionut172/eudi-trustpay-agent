"""EUDI trust specialist agent."""

from google.adk.agents import Agent
from app.config import Settings
from app.tools.payment_tools import (
    check_payee_eudi_verification,
    initiate_payee_eudi_verification,
)


EUDI_INSTRUCTION = """Handle only external EUDI Wallet verification.
Start verification for the exact pending recipient and payment.
Check the verifier's current result; never accept conversational claims as proof.
Block on mismatch or trust errors. Explain recovery when an IBAN attestation is missing.
"""


def create_eudi_agent(settings: Settings) -> Agent:
    """Build the wallet-verification specialist."""
    return Agent(
        name="eudi_trust_agent",
        description="Initiates and checks external EUDI Wallet verification",
        instruction=EUDI_INSTRUCTION,
        model=settings.MODEL_NAME,
        tools=[initiate_payee_eudi_verification, check_payee_eudi_verification],
    )
