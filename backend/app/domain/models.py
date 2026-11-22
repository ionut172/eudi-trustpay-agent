"""Strict Pydantic domain models for EUDI Wallet & Instant Payments."""

import datetime
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, computed_field


class VerificationStatus(str, Enum):
    """Lifecycle status of EUDI Wallet payee verification."""

    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    IBAN_MISMATCH = "IBAN_MISMATCH"
    TRUST_ERROR = "TRUST_ERROR"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMPLETED = "COMPLETED"
    FAST_PATH_APPROVED = "FAST_PATH_APPROVED"
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"


class PIDCredential(BaseModel):
    """European Person Identification Data (PID) Verifiable Credential."""

    sub: str = Field(description="Subject identifier (e.g., urn:eu:pid:DE-123456789)")
    given_name: str = Field(description="Payee given name(s)")
    family_name: str = Field(description="Payee family name")
    full_name_override: Optional[str] = Field(default=None, alias="full_name", description="Optional full name override")
    birth_date: Optional[str] = Field(default="1990-01-01", description="Date of birth")
    is_valid: bool = Field(default=True, description="Cryptographic trust and revocation status of PID")
    issuer: str = Field(default="eu-pid-authority", description="PID issuing trust authority")

    @computed_field
    def full_name(self) -> str:
        if self.full_name_override:
            return self.full_name_override.strip()
        return f"{self.given_name} {self.family_name}".strip()


class IBANAttestationCredential(BaseModel):
    """Bank-issued IBAN Account Ownership Attestation Verifiable Credential."""

    sub: Optional[str] = Field(default=None, description="Subject identifier bound to the account owner")
    iban: Optional[str] = Field(default=None, description="International Bank Account Number")
    account_holder_name: Optional[str] = Field(default=None, description="Account holder name at bank")
    bank_name: str = Field(default="Demo Issuer Bank", description="Synthetic issuer name used by the mock adapter")
    is_valid: bool = Field(default=True, description="Cryptographic signature status")
    is_expired: bool = Field(default=False, description="Whether attestation validity window expired")


class DCQLRequest(BaseModel):
    """OID4VP Digital Credentials Query Language (DCQL) presentation request."""

    request_id: str
    session_id: str
    target_iban: str
    expected_payee_name: str
    amount: float
    currency: str = "EUR"
    nonce: str
    verifier_callback_url: str
    query: dict[str, Any]
    qr_code_uri: str
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))


class WalletPresentationPayload(BaseModel):
    """Raw OID4VP presentation payload submitted by the EUDI Wallet."""

    session_id: str
    pid: Optional[PIDCredential] = None
    iban_attestation: Optional[IBANAttestationCredential] = None
    presentation_submission: Optional[dict[str, Any]] = None
    nonce: Optional[str] = None


class WalletVerificationChecks(BaseModel):
    """Evaluation details returned for a wallet presentation in mock or live mode."""

    presentation_received: bool = False
    credential_signatures_valid: bool = False
    credential_issuers_trusted: bool = False
    credentials_not_expired: bool = False
    credential_status_valid: bool = False
    holder_binding_valid: bool = False
    account_holder_match: bool = False


class PayeeVerificationResult(BaseModel):
    """Outcome of EUDI Wallet presentation evaluation."""

    verification_id: Optional[str] = None
    request_id: Optional[str] = None
    status: VerificationStatus
    mode: Literal["mock", "live"] = "mock"
    session_id: str
    verified_name: Optional[str] = None
    verified_iban: Optional[str] = None
    issuer_bank: Optional[str] = None
    reason: Optional[str] = None
    recovery_issuer_url: Optional[str] = None
    actual_pid_name: Optional[str] = None
    actual_iban_owner: Optional[str] = None
    checks: Optional[WalletVerificationChecks] = None
    verified_at: Optional[datetime.datetime] = None
    timestamp: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))


class PaymentExecutionRequest(BaseModel):
    """Request payload for executing instant SEPA payment."""

    session_id: str
    payment_id: str
    debtor_account: str = "NL91INGB0009876543"
    creditor_iban: str
    creditor_name: str
    amount: float
    currency: str = "EUR"
    reference: str = "Conversational Payment via EUDI Wallet"


class PaymentAuthorization(BaseModel):
    """Backend-issued authorization bound to one exact payment instruction."""

    decision: Literal["EUDI_VERIFIED", "TRUSTED_FAVORITE"]
    session_id: str
    payment_id: str
    recipient_name: str
    recipient_iban: str
    amount: float = Field(gt=0)
    currency: str = "EUR"
    verification_id: Optional[str] = None
    favorite_name: Optional[str] = None
    max_amount: Optional[float] = None
    reason: str
    authorized_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))


class PaymentReceiptVC(BaseModel):
    """Signed Verifiable Credential issued to EUDI Wallet via OID4VCI upon payment."""

    credential_id: str
    transaction_id: str
    session_id: str
    payment_id: str = ""
    amount: float
    currency: str = "EUR"
    debtor_iban: str
    creditor_iban: str
    creditor_name: str
    status: str = "EXECUTED"
    timestamp: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    issuer_did: str = "did:web:issuer.example.com"
    issuer_signature: str


class PendingPayment(BaseModel):
    """Payment intent tracked in conversation session state."""

    amount: float
    currency: str = "EUR"
    creditor_iban: str
    creditor_name: str
    reference: str = "Conversational payment"


class ChatRequest(BaseModel):
    """Inbound chat request from the conversational UI."""

    session_id: str
    user_id: str = "user-1"
    message: str


class ChatResponse(BaseModel):
    """Outbound chat response with updated session state."""

    session_id: str
    reply: str
    state: dict[str, Any]


class PaymentIntent(BaseModel):
    """Structured payment intent extracted by the intent agent or API client."""

    recipient_name: Optional[str] = None
    recipient_iban: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    currency: str = "EUR"
    clarification_needed: Optional[str] = None


class PaymentIntentRequest(BaseModel):
    """Typed payment-intent endpoint for integrations without an LLM."""

    session_id: str
    user_id: str = "user-1"
    intent: PaymentIntent


class PaymentRiskAssessment(BaseModel):
    """Business risk decision produced by the intent/risk agent."""

    route: Literal["FAST_PATH_APPROVED", "STEP_UP_REQUIRED", "CLARIFY"]
    recipient_name: Optional[str] = None
    recipient_iban: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "EUR"
    reason: str
