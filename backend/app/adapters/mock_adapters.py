"""Stateful mock adapters for EUDI Wallet verifier and core banking with file-backed cross-process persistence."""

import datetime
import json
import secrets
import uuid
from pathlib import Path
from typing import Optional
from app.adapters.base import ICoreBankingPort, IEUDIVerifierPort
from app.domain.models import (
    DCQLRequest,
    IBANAttestationCredential,
    PayeeVerificationResult,
    PaymentExecutionRequest,
    PaymentReceiptVC,
    PIDCredential,
    VerificationStatus,
    WalletPresentationPayload,
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

    def __init__(self, recovery_issuer_url: str = "https://bank.ing.com/wallet-issuance/iban-attestation"):
        self.recovery_issuer_url = recovery_issuer_url
        self._requests: dict[str, DCQLRequest] = {}
        self._presentations: dict[str, WalletPresentationPayload] = {}
        self._results: dict[str, PayeeVerificationResult] = {}
        self._sync_from_disk()

    def _sync_from_disk(self) -> None:
        """Loads state from shared disk file."""
        state = _read_persistent_state()
        for s_id, req_data in state.get("requests", {}).items():
            try:
                self._requests[s_id] = DCQLRequest.model_validate(req_data)
            except Exception:
                pass
        for s_id, pres_data in state.get("presentations", {}).items():
            try:
                self._presentations[s_id] = WalletPresentationPayload.model_validate(pres_data)
            except Exception:
                pass
        for s_id, res_data in state.get("results", {}).items():
            try:
                self._results[s_id] = PayeeVerificationResult.model_validate(res_data)
            except Exception:
                pass

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
        verifier_callback_url = f"https://verifier.eudi.ing.com/api/v1/sessions/{session_id}/presentation"

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
            f"openid4vp://?client_id=https://verifier.eudi.ing.com&request_uri={verifier_callback_url}"
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
        self._presentations[session_id] = payload
        req = self._requests.get(session_id)

        target_iban = req.target_iban if req else (payload.iban_attestation.iban if payload.iban_attestation else "")
        expected_name = (
            req.expected_payee_name
            if req
            else (payload.pid.full_name if payload.pid else "")
        )

        result = evaluate_wallet_presentation(
            payload=payload,
            expected_iban=target_iban,
            expected_payee_name=expected_name,
            recovery_issuer_url=self.recovery_issuer_url,
        )

        from app.domain.models import WalletVerificationChecks

        is_verified = result.status == VerificationStatus.VERIFIED
        is_rejection = result.status in {
            VerificationStatus.SUBJECT_MISMATCH,
            VerificationStatus.IBAN_MISMATCH,
            VerificationStatus.TRUST_ERROR,
        }
        result.verification_id = f"ver_test_{session_id}"
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
            else:
                result.reason = "ACCOUNT_HOLDER_MISMATCH" if result.status == VerificationStatus.SUBJECT_MISMATCH else result.reason

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
            bank_name="ING Bank N.V.",
            is_valid=True,
            is_expired=False,
        )

        updated_payload = WalletPresentationPayload(
            session_id=session_id,
            pid=pid,
            iban_attestation=recovered_attestation,
        )

        self._presentations[session_id] = updated_payload
        result = evaluate_wallet_presentation(
            payload=updated_payload,
            expected_iban=target_iban,
            expected_payee_name=expected_name,
            recovery_issuer_url=self.recovery_issuer_url,
        )
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

        if scenario == "valid":
            payload = WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:NL-12345678",
                    given_name=expected_name.split()[0] if expected_name else "Alice",
                    family_name=" ".join(expected_name.split()[1:]) if len(expected_name.split()) > 1 else "Visser",
                    is_valid=True,
                ),
                iban_attestation=IBANAttestationCredential(
                    sub="urn:eu:pid:NL-12345678",
                    iban=target_iban,
                    account_holder_name=expected_name,
                    bank_name="ING Bank N.V.",
                    is_valid=True,
                    is_expired=False,
                ),
            )
        elif scenario == "mismatch":
            payload = WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:NL-88888888",
                    given_name="Eve",
                    family_name="Malicious",
                    is_valid=True,
                ),
                iban_attestation=IBANAttestationCredential(
                    sub="urn:eu:pid:NL-99999999",
                    iban=target_iban,
                    account_holder_name="Bob Mule",
                    bank_name="ING Bank N.V.",
                    is_valid=True,
                    is_expired=False,
                ),
            )
        elif scenario == "missing_iban":
            payload = WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:NL-12345678",
                    given_name=expected_name.split()[0] if expected_name else "Alice",
                    family_name=" ".join(expected_name.split()[1:]) if len(expected_name.split()) > 1 else "Visser",
                    is_valid=True,
                ),
                iban_attestation=None,
            )
        else:
            raise ValueError(f"Unknown preset scenario '{scenario}'. Supported: valid, mismatch, missing_iban")

        return await self.submit_wallet_presentation(session_id=session_id, payload=payload)


class MockCoreBankingAdapter(ICoreBankingPort):
    """Core Banking adapter with cross-process persistence for issued receipts."""

    def __init__(self):
        self._receipts: dict[str, list[PaymentReceiptVC]] = {}
        self._sync_from_disk()

    def _sync_from_disk(self) -> None:
        state = _read_persistent_state()
        for s_id, receipts_data in state.get("receipts", {}).items():
            try:
                self._receipts[s_id] = [PaymentReceiptVC.model_validate(r) for r in receipts_data]
            except Exception:
                pass

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
        verification: PayeeVerificationResult,
    ) -> PaymentReceiptVC:
        self._sync_from_disk()
        session_id = payment_request.session_id

        if verification.status != VerificationStatus.VERIFIED:
            raise ValueError(
                f"Core banking refused execution: payee status is '{verification.status.value}', not 'VERIFIED'."
            )

        transaction_id = f"TX-SEPA-INSTANT-{uuid.uuid4().hex[:12].upper()}"
        credential_id = f"urn:uuid:{uuid.uuid4()}"
        now = datetime.datetime.now(datetime.timezone.utc)
        simulated_signature = f"rsa-sha256:signed:ing-mock-keystore:{secrets.token_hex(32)}"

        receipt = PaymentReceiptVC(
            credential_id=credential_id,
            transaction_id=transaction_id,
            session_id=session_id,
            amount=payment_request.amount,
            currency=payment_request.currency,
            debtor_iban=payment_request.debtor_account,
            creditor_iban=payment_request.creditor_iban,
            creditor_name=payment_request.creditor_name,
            status="EXECUTED",
            timestamp=now,
            issuer_did="did:web:bank.ing.com",
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
