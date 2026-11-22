"""Stateful mock adapters for EUDI Wallet verifier and core banking with file-backed cross-process persistence."""

import datetime
import json
import secrets
import uuid
from pathlib import Path
from typing import Optional
from app.adapters.base import ICoreBankingPort, IEUDIVerifierPort
from app.adapters.mock_wallet_api import MockEUWalletApi
from app.domain.models import (
    DCQLRequest,
    IBANAttestationCredential,
    PayeeVerificationResult,
    PaymentAuthorization,
    PaymentExecutionRequest,
    PaymentReceiptVC,
    PIDCredential,
    VerificationStatus,
    WalletPresentationPayload,
    WalletVerificationChecks,
)
from app.domain.validators import evaluate_wallet_presentation
from app.telemetry import get_logger

logger = get_logger("mock_adapters")

STATE_FILE = Path("/tmp/ing_mock_verifier_state.json")


def _read_persistent_state() -> dict:
    """Reads shared state across uvicorn, adk web, and CLI processes."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read persistent mock state from {STATE_FILE}: {e}")
            return {}
    return {}


def _write_persistent_state(data: dict) -> None:
    """Writes shared state across processes atomically."""
    try:
        temp_file = STATE_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        temp_file.replace(STATE_FILE)
    except Exception as e:
        logger.warning(f"Failed to write persistent mock state to {STATE_FILE}: {e}")


class MockEUDIVerifierAdapter(IEUDIVerifierPort):
    """OID4VP Verifier simulator with cross-process persistence for DCQL and presets."""

    def __init__(self, recovery_issuer_url: str = "https://issuer.example.com/attestations/iban"):
        self.recovery_issuer_url = recovery_issuer_url
        self._requests: dict[str, DCQLRequest] = {}
        self._presentations: dict[str, WalletPresentationPayload] = {}
        self._results: dict[str, PayeeVerificationResult] = {}
        self._wallet_api = MockEUWalletApi()
        self._sync_from_disk()

    def _sync_from_disk(self) -> None:
        """Loads state from shared disk file."""
        state = _read_persistent_state()
        requests: dict[str, DCQLRequest] = {}
        presentations: dict[str, WalletPresentationPayload] = {}
        results: dict[str, PayeeVerificationResult] = {}
        for s_id, req_data in state.get("requests", {}).items():
            try:
                requests[s_id] = DCQLRequest.model_validate(req_data)
            except Exception:
                pass
        for s_id, pres_data in state.get("presentations", {}).items():
            try:
                presentations[s_id] = WalletPresentationPayload.model_validate(pres_data)
            except Exception:
                pass
        for s_id, res_data in state.get("results", {}).items():
            try:
                results[s_id] = PayeeVerificationResult.model_validate(res_data)
            except Exception:
                pass
        self._requests = requests
        self._presentations = presentations
        self._results = results

    def _persist_to_disk(self) -> None:
        """Saves current state to shared disk file."""
        state = _read_persistent_state()
        state["requests"] = {k: v.model_dump(mode="json") for k, v in self._requests.items()}
        state["presentations"] = {k: v.model_dump(mode="json") for k, v in self._presentations.items()}
        state["results"] = {k: v.model_dump(mode="json") for k, v in self._results.items()}
        _write_persistent_state(state)

    async def create_dcql_request(
        self,
        session_id: str,
        target_iban: str,
        expected_payee_name: str,
        amount: float,
        currency: str = "EUR",
    ) -> DCQLRequest:
        self._sync_from_disk()
        request_id = f"dcql-{uuid.uuid4().hex[:12]}"
        nonce = secrets.token_urlsafe(16)
        verifier_callback_url = f"https://verifier.example.com/api/v1/sessions/{session_id}/presentation"

        # DCQL query asking for EU PID and bank-issued IBAN attestation
        dcql_query = {
            "credentials": [
                {
                    "id": "eu_pid",
                    "format": "mso_mdoc",
                    "doctype": "eu.europa.ec.eudi.pid.1",
                    "claims": ["family_name", "given_name", "birth_date", "sub"],
                },
                {
                    "id": "iban_attestation",
                    "format": "sd_jwt_vc",
                    "vct": "urn:eu:europa:ec:eudi:iban_attestation:1",
                    "claims": ["iban", "sub", "account_holder_name", "bank_name"],
                },
            ]
        }

        qr_code_uri = (
            f"openid4vp://?client_id=https://verifier.example.com&request_uri={verifier_callback_url}"
            f"&response_type=vp_token&nonce={nonce}"
        )

        req = DCQLRequest(
            request_id=request_id,
            session_id=session_id,
            target_iban=target_iban,
            expected_payee_name=expected_payee_name,
            amount=amount,
            currency=currency,
            nonce=nonce,
            verifier_callback_url=verifier_callback_url,
            query=dcql_query,
            qr_code_uri=qr_code_uri,
        )

        # A session may initiate another payment. Never let a prior presentation or
        # verdict authorize the new request.
        self._presentations.pop(session_id, None)
        self._results.pop(session_id, None)
        self._requests[session_id] = req
        self._persist_to_disk()

        logger.log_event(
            severity="INFO",
            message=f"Created DCQL presentation request for payee '{expected_payee_name}' (IBAN: {target_iban})",
            event_type="dcql_request_created",
            session_id=session_id,
            request_id=request_id,
            target_iban=target_iban,
            amount=amount,
        )

        return req

    async def get_dcql_request(self, session_id: str) -> Optional[DCQLRequest]:
        self._sync_from_disk()
        return self._requests.get(session_id)

    async def submit_wallet_presentation(
        self,
        session_id: str,
        payload: WalletPresentationPayload,
    ) -> PayeeVerificationResult:
        self._sync_from_disk()
        req = self._requests.get(session_id)

        target_iban = req.target_iban if req else (payload.iban_attestation.iban if payload.iban_attestation else "")
        expected_name = (
            req.expected_payee_name
            if req
            else (payload.pid.full_name if payload.pid else "")
        )

        if req is None or payload.session_id != session_id or payload.nonce != req.nonce:
            return PayeeVerificationResult(
                status=VerificationStatus.TRUST_ERROR,
                session_id=session_id,
                request_id=req.request_id if req else None,
                verification_id=f"ver_{uuid.uuid4().hex}",
                mode="mock",
                reason="Presentation is not bound to the active wallet request.",
            )

        self._presentations[session_id] = payload
        result = evaluate_wallet_presentation(
            payload=payload,
            expected_iban=target_iban,
            expected_payee_name=expected_name,
            recovery_issuer_url=self.recovery_issuer_url,
        )

        is_verified = result.status == VerificationStatus.VERIFIED
        is_rejection = result.status in {
            VerificationStatus.SUBJECT_MISMATCH,
            VerificationStatus.IBAN_MISMATCH,
            VerificationStatus.TRUST_ERROR,
        }
        result.verification_id = f"ver_{uuid.uuid4().hex}"
        result.request_id = req.request_id
        result.mode = "mock"
        result.checks = WalletVerificationChecks(
            presentation_received=True,
            credential_signatures_valid=is_verified,
            credential_issuers_trusted=is_verified,
            credentials_not_expired=is_verified,
            credential_status_valid=is_verified,
            holder_binding_valid=is_verified,
            account_holder_match=is_verified,
        )
        if is_verified:
            result.verified_at = datetime.datetime.now(datetime.timezone.utc)
        elif is_rejection:
            if result.status == VerificationStatus.SUBJECT_MISMATCH:
                result.status = VerificationStatus.REJECTED
                result.reason = "ACCOUNT_HOLDER_MISMATCH"
            elif result.status == VerificationStatus.IBAN_MISMATCH:
                result.status = VerificationStatus.REJECTED
                result.reason = "IBAN_MISMATCH"

        self._results[session_id] = result
        self._persist_to_disk()

        logger.log_event(
            severity="WARNING" if result.status != VerificationStatus.VERIFIED else "INFO",
            message=f"Evaluated wallet presentation: status={result.status.value}, reason={result.reason}",
            event_type="wallet_validation_result",
            session_id=session_id,
            status=result.status.value,
            verified_name=result.verified_name,
            verified_iban=result.verified_iban,
            actual_pid_name=result.actual_pid_name,
            actual_iban_owner=result.actual_iban_owner,
        )

        return result

    async def get_verification_result(self, session_id: str) -> Optional[PayeeVerificationResult]:
        self._sync_from_disk()
        return self._results.get(session_id)

    async def recover_iban_attestation(self, session_id: str) -> PayeeVerificationResult:
        self._sync_from_disk()
        req = self._requests.get(session_id)
        if not req:
            raise ValueError(
                f"No active EUDI verification request found for session '{session_id}'. "
                "The payment must be initiated in chat first before requesting recovery."
            )
        target_iban = req.target_iban
        expected_name = req.expected_payee_name

        existing_presentation = self._presentations.get(session_id)
        pid = (
            existing_presentation.pid
            if existing_presentation and existing_presentation.pid
            else PIDCredential(
                sub="urn:eu:pid:NL-12345678",
                given_name=expected_name.split()[0] if expected_name else "Alice",
                family_name=" ".join(expected_name.split()[1:]) if len(expected_name.split()) > 1 else "Visser",
                is_valid=True,
            )
        )

        recovered_attestation = IBANAttestationCredential(
            sub=pid.sub,
            iban=target_iban,
            account_holder_name=pid.full_name,
            bank_name="Demo Issuer Bank",
            is_valid=True,
            is_expired=False,
        )

        updated_payload = WalletPresentationPayload(
            session_id=session_id,
            pid=pid,
            iban_attestation=recovered_attestation,
            nonce=req.nonce,
        )

        self._presentations[session_id] = updated_payload
        result = evaluate_wallet_presentation(
            payload=updated_payload,
            expected_iban=target_iban,
            expected_payee_name=expected_name,
            recovery_issuer_url=self.recovery_issuer_url,
        )
        result.verification_id = f"ver_{uuid.uuid4().hex}"
        result.request_id = req.request_id
        result.mode = "mock"
        if result.status == VerificationStatus.VERIFIED:
            result.verified_at = datetime.datetime.now(datetime.timezone.utc)
        self._results[session_id] = result
        self._persist_to_disk()

        logger.log_event(
            severity="INFO",
            message=f"Issued missing IBAN attestation via bank recovery for session {session_id}. Re-evaluated to VERIFIED.",
            event_type="recovery_issued",
            session_id=session_id,
            verified_iban=result.verified_iban,
            verified_name=result.verified_name,
        )

        return result

    async def apply_preset_scenario(self, session_id: str, scenario: str) -> PayeeVerificationResult:
        self._sync_from_disk()
        req = self._requests.get(session_id)
        if not req:
            raise ValueError(
                f"No active EUDI verification request found for session '{session_id}'. "
                "The payment must be initiated in chat first before presenting credentials."
            )
        target_iban = req.target_iban
        expected_name = req.expected_payee_name

        scenario = scenario.strip().lower()

        try:
            payload = await self._wallet_api.fetch_presentation(
                session_id=session_id,
                expected_name=expected_name,
                target_iban=target_iban,
                scenario=scenario,
            )
        except ValueError:
            raise ValueError(
                f"Unknown preset scenario '{scenario}'. "
                "Supported: valid, mismatch, iban_mismatch, missing_iban"
            )

        payload.nonce = req.nonce
        return await self.submit_wallet_presentation(session_id=session_id, payload=payload)


class MockCoreBankingAdapter(ICoreBankingPort):
    """Core Banking adapter with cross-process persistence for issued receipts."""

    def __init__(self):
        self._receipts: dict[str, list[PaymentReceiptVC]] = {}
        self._sync_from_disk()

    def _sync_from_disk(self) -> None:
        state = _read_persistent_state()
        receipts: dict[str, list[PaymentReceiptVC]] = {}
        for s_id, receipts_data in state.get("receipts", {}).items():
            try:
                receipts[s_id] = [PaymentReceiptVC.model_validate(r) for r in receipts_data]
            except Exception:
                pass
        self._receipts = receipts

    def _persist_to_disk(self) -> None:
        state = _read_persistent_state()
        state["receipts"] = {
            k: [r.model_dump(mode="json") for r in v]
            for k, v in self._receipts.items()
        }
        _write_persistent_state(state)

    async def execute_instant_payment(
        self,
        payment_request: PaymentExecutionRequest,
        authorization: PaymentAuthorization,
        verification: Optional[PayeeVerificationResult] = None,
    ) -> PaymentReceiptVC:
        self._sync_from_disk()
        session_id = payment_request.session_id

        payment_matches_authorization = (
            authorization.session_id == session_id
            and authorization.payment_id == payment_request.payment_id
            and authorization.recipient_iban.replace(" ", "").upper()
            == payment_request.creditor_iban.replace(" ", "").upper()
            and authorization.recipient_name.strip().casefold()
            == payment_request.creditor_name.strip().casefold()
            and authorization.amount == payment_request.amount
            and authorization.currency == payment_request.currency
        )
        if not payment_matches_authorization:
            raise ValueError("Core banking refused execution: authorization does not match payment details.")

        if not payment_request.payment_id or authorization.payment_id != payment_request.payment_id:
            raise ValueError("Core banking refused execution: payment id is missing or mismatched.")

        if authorization.decision == "EUDI_VERIFIED":
            if (
                verification is None
                or verification.status != VerificationStatus.VERIFIED
                or authorization.verification_id != verification.verification_id
                or (verification.verified_iban or "").replace(" ", "").upper()
                != authorization.recipient_iban.replace(" ", "").upper()
            ):
                raise ValueError("Core banking refused execution: verified EUDI record is missing or unbound.")
        elif authorization.decision == "TRUSTED_FAVORITE":
            if (
                verification is not None
                or not authorization.favorite_name
                or authorization.favorite_name.strip().casefold()
                != authorization.recipient_name.strip().casefold()
                or authorization.max_amount is None
                or payment_request.amount > authorization.max_amount
            ):
                raise ValueError("Core banking refused execution: trusted-favorite authorization is invalid.")

        for existing_receipt in self._receipts.get(session_id, []):
            if existing_receipt.payment_id == payment_request.payment_id:
                same_payment = (
                    existing_receipt.amount == payment_request.amount
                    and existing_receipt.currency == payment_request.currency
                    and existing_receipt.creditor_iban.replace(" ", "").upper()
                    == payment_request.creditor_iban.replace(" ", "").upper()
                    and existing_receipt.creditor_name.strip().casefold()
                    == payment_request.creditor_name.strip().casefold()
                    and existing_receipt.debtor_iban.replace(" ", "").upper()
                    == payment_request.debtor_account.replace(" ", "").upper()
                )
                if not same_payment:
                    raise ValueError(
                        "Core banking refused execution: payment id was reused with different details."
                    )
                return existing_receipt

        transaction_id = f"TX-SEPA-INSTANT-{uuid.uuid4().hex[:12].upper()}"
        credential_id = f"urn:uuid:{uuid.uuid4()}"
        now = datetime.datetime.now(datetime.timezone.utc)
        simulated_signature = f"rsa-sha256:signed:demo-mock-keystore:{secrets.token_hex(32)}"

        receipt = PaymentReceiptVC(
            credential_id=credential_id,
            transaction_id=transaction_id,
            session_id=session_id,
            payment_id=payment_request.payment_id,
            amount=payment_request.amount,
            currency=payment_request.currency,
            debtor_iban=payment_request.debtor_account,
            creditor_iban=payment_request.creditor_iban,
            creditor_name=payment_request.creditor_name,
            status="EXECUTED",
            timestamp=now,
            issuer_did="did:web:issuer.example.com",
            issuer_signature=simulated_signature,
        )

        if session_id not in self._receipts:
            self._receipts[session_id] = []
        self._receipts[session_id].append(receipt)
        self._persist_to_disk()

        logger.log_event(
            severity="INFO",
            message=f"Executed instant SEPA transfer of {receipt.amount} {receipt.currency} to {receipt.creditor_name}",
            event_type="payment_executed",
            session_id=session_id,
            transaction_id=transaction_id,
            amount=receipt.amount,
            creditor_iban=receipt.creditor_iban,
        )

        logger.log_event(
            severity="INFO",
            message=f"Issued OID4VCI PaymentReceipt credential '{credential_id}' to payee wallet",
            event_type="payment_receipt_issued",
            session_id=session_id,
            credential_id=credential_id,
            transaction_id=transaction_id,
        )

        return receipt

    async def get_receipts(self, session_id: str) -> list[PaymentReceiptVC]:
        self._sync_from_disk()
        return self._receipts.get(session_id, [])
