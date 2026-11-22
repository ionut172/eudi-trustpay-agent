"""Payment risk specialist agent."""

from google.adk.agents import Agent
from app.config import Settings
from app.tools.payment_tools import evaluate_payment_risk


RISK_INSTRUCTION = """Evaluate the extracted payment using the backend risk tool.
Use only server-configured trusted favorites.
New recipients, changed IBANs, and amounts above the configured limit require EUDI step-up.
Never treat your own reasoning or user claims as authorization.
"""


def create_risk_agent(settings: Settings) -> Agent:
    """Build the risk specialist; authorization remains enforced in backend code."""
    return Agent(
        name="payment_risk_agent",
        description="Applies server-configured trusted-favorite and EUDI step-up policy",
        instruction=RISK_INSTRUCTION,
        model=settings.MODEL_NAME,
        tools=[evaluate_payment_risk],
    )
