"""Async HTTPX live adapter stubs for production EUDI Verifier & Core Banking APIs."""

from typing import Optional, Tuple
import httpx
from app.adapters.base import ICoreBankingPort, IEUDIVerifierPort
from app.adapters.mock_adapters import MockCoreBankingAdapter, MockEUDIVerifierAdapter
from app.config import Settings, get_settings
from app.domain.models import (
    DCQLRequest,
    PayeeVerificationResult,
    PaymentAuthorization,
    PaymentExecutionRequest,
    PaymentReceiptVC,
    WalletPresentationPayload,
)
from app.telemetry import get_logger

logger = get_logger("live_adapters")


class LiveEUDIVerifierAdapter(IEUDIVerifierPort):
    """Production-ready async client communicating with real external OID4VP Verifiers."""

    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def create_dcql_request(
        self,
        session_id: str,
        target_iban: str,
        expected_payee_name: str,
        amount: float,
        currency: str = "EUR",
    ) -> DCQLRequest:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/dcql-requests"
        payload = {
            "target_iban": target_iban,
            "expected_payee_name": expected_payee_name,
            "amount": amount,
            "currency": currency,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return DCQLRequest.model_validate(data)

    async def get_dcql_request(self, session_id: str) -> Optional[DCQLRequest]:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/dcql-requests"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return DCQLRequest.model_validate(resp.json())

    async def submit_wallet_presentation(
        self,
        session_id: str,
        payload: WalletPresentationPayload,
    ) -> PayeeVerificationResult:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/presentation"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload.model_dump(mode="json"))
            resp.raise_for_status()
            return PayeeVerificationResult.model_validate(resp.json())

    async def get_verification_result(self, session_id: str) -> Optional[PayeeVerificationResult]:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/verification-result"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return PayeeVerificationResult.model_validate(resp.json())

    async def recover_iban_attestation(self, session_id: str) -> PayeeVerificationResult:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/recover-iban"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url)
            resp.raise_for_status()
            return PayeeVerificationResult.model_validate(resp.json())

    async def apply_preset_scenario(self, session_id: str, scenario: str) -> PayeeVerificationResult:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/preset/{scenario}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url)
            resp.raise_for_status()
            return PayeeVerificationResult.model_validate(resp.json())


class LiveCoreBankingAdapter(ICoreBankingPort):
    """Production-ready async client for Core Banking SEPA Instant payment API."""

    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def execute_instant_payment(
        self,
        payment_request: PaymentExecutionRequest,
        authorization: PaymentAuthorization,
        verification: Optional[PayeeVerificationResult] = None,
    ) -> PaymentReceiptVC:
        url = f"{self.base_url}/api/v1/payments/instant-sepa"
        payload = {
            "payment": payment_request.model_dump(mode="json"),
            "authorization": authorization.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json") if verification else None,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return PaymentReceiptVC.model_validate(resp.json())

    async def get_receipts(self, session_id: str) -> list[PaymentReceiptVC]:
        url = f"{self.base_url}/api/v1/sessions/{session_id}/receipts"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return [PaymentReceiptVC.model_validate(r) for r in resp.json()]


# Global adapter registry
_global_verifier: Optional[IEUDIVerifierPort] = None
_global_core_banking: Optional[ICoreBankingPort] = None


def get_adapters(
    settings: Optional[Settings] = None,
) -> Tuple[IEUDIVerifierPort, ICoreBankingPort]:
    """Factory creating or returning the configured verifier and banking adapters."""
    global _global_verifier, _global_core_banking
    if _global_verifier is not None and _global_core_banking is not None:
        return _global_verifier, _global_core_banking

    if settings is None:
        settings = get_settings()

    if settings.ADAPTER_MODE.upper() == "LIVE":
        logger.info("Initializing LIVE external HTTPX adapters", mode="LIVE")
        _global_verifier = LiveEUDIVerifierAdapter(
            base_url=settings.LIVE_VERIFIER_URL,
            timeout_seconds=settings.VERIFIER_TIMEOUT_SECONDS,
        )
        _global_core_banking = LiveCoreBankingAdapter(
            base_url=settings.LIVE_CORE_BANKING_URL,
            timeout_seconds=settings.VERIFIER_TIMEOUT_SECONDS,
        )
    else:
        logger.info("Initializing in-memory MOCK adapters", mode="MOCK")
        _global_verifier = MockEUDIVerifierAdapter(recovery_issuer_url=settings.RECOVERY_ISSUER_URL)
        _global_core_banking = MockCoreBankingAdapter()

    return _global_verifier, _global_core_banking
