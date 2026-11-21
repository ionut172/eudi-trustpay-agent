"""Abstract ports defining hexagonal boundaries for external EUDI & Banking systems."""

from abc import ABC, abstractmethod
from typing import Optional
from app.domain.models import (
    DCQLRequest,
    PayeeVerificationResult,
    PaymentExecutionRequest,
    PaymentReceiptVC,
    WalletPresentationPayload,
)


class IEUDIVerifierPort(ABC):
    """Abstract port for EUDI Wallet OID4VP Verifier interaction."""

    @abstractmethod
    async def create_dcql_request(
        self,
        session_id: str,
        target_iban: str,
        expected_payee_name: str,
        amount: float,
        currency: str = "EUR",
    ) -> DCQLRequest:
        """Generates an OID4VP DCQL presentation request for payee identity verification."""
        pass

    @abstractmethod
    async def get_dcql_request(self, session_id: str) -> Optional[DCQLRequest]:
        """Retrieves active DCQL request for given session."""
        pass

    @abstractmethod
    async def submit_wallet_presentation(
        self,
        session_id: str,
        payload: WalletPresentationPayload,
    ) -> PayeeVerificationResult:
        """Processes and evaluates incoming EUDI Wallet presentation."""
        pass

    @abstractmethod
    async def get_verification_result(self, session_id: str) -> Optional[PayeeVerificationResult]:
        """Retrieves the latest payee verification outcome for the session."""
        pass

    @abstractmethod
    async def recover_iban_attestation(self, session_id: str) -> PayeeVerificationResult:
        """Simulates issuing missing IBAN attestation from bank and re-evaluates session."""
        pass

    @abstractmethod
    async def apply_preset_scenario(self, session_id: str, scenario: str) -> PayeeVerificationResult:
        """Applies pre-configured test scenarios (valid, mismatch, missing_iban)."""
        pass


class ICoreBankingPort(ABC):
    """Abstract port for core banking instant payment execution and receipt VC issuance."""

    @abstractmethod
    async def execute_instant_payment(
        self,
        payment_request: PaymentExecutionRequest,
        verification: PayeeVerificationResult,
    ) -> PaymentReceiptVC:
        """Executes instant SEPA transfer and issues an OID4VCI PaymentReceipt credential."""
        pass

    @abstractmethod
    async def get_receipts(self, session_id: str) -> list[PaymentReceiptVC]:
        """Retrieves verifiable receipts issued for the session."""
        pass
