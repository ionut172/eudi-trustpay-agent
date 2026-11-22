"""Pure security and identity validators for EUDI Wallet presentations."""

import re
from typing import Optional
from app.domain.models import (
    PayeeVerificationResult,
    VerificationStatus,
    WalletPresentationPayload,
)


def _normalize_iban(iban: Optional[str]) -> str:
    """Strips all whitespace and converts IBAN to uppercase."""
    if not iban:
        return ""
    return re.sub(r"\s+", "", iban).upper()


def _normalize_name(name: Optional[str]) -> str:
    """Normalizes whitespace and casing for name comparison."""
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def evaluate_wallet_presentation(
    payload: WalletPresentationPayload,
    expected_iban: str,
    expected_payee_name: str,
    recovery_issuer_url: str = "https://issuer.example.com/attestations/iban",
) -> PayeeVerificationResult:
    """Evaluates an OID4VP wallet presentation in strict security precedence order:

    1. PID Cryptographic Validity: If pid_is_valid is False -> TRUST_ERROR.
    2. Recovery Trigger: If iban_value/iban_sub is missing, or iban_is_expired -> RECOVERY_REQUIRED.
    3. IBAN Attestation Trust Check: If iban_is_valid is False -> TRUST_ERROR.
    4. Subject Binding Check: If pid_sub != iban_sub -> SUBJECT_MISMATCH.
    5. Target IBAN & Payee Name Match:
       - If normalized IBAN differs -> IBAN_MISMATCH.
       - If expected_name differs from pid_full_name -> SUBJECT_MISMATCH (exposing real account holder).
    6. Verified: Returns VERIFIED with verified_name, verified_iban, and issuer_bank.
    """
    session_id = payload.session_id

    # Rule 1: PID Cryptographic Validity
    if payload.pid is None or not payload.pid.is_valid:
        return PayeeVerificationResult(
            status=VerificationStatus.TRUST_ERROR,
            session_id=session_id,
            reason="PID cryptographic signature is invalid or issued by an untrusted authority.",
        )

    # Rule 2: Recovery Trigger (Missing or Expired IBAN Attestation)
    iban_attestation = payload.iban_attestation
    if (
        iban_attestation is None
        or not iban_attestation.iban
        or not iban_attestation.sub
        or iban_attestation.is_expired
    ):
        return PayeeVerificationResult(
            status=VerificationStatus.RECOVERY_REQUIRED,
            session_id=session_id,
            recovery_issuer_url=recovery_issuer_url,
            reason="IBAN Account Ownership Attestation is missing or expired in the presented EUDI Wallet.",
        )

    # Rule 3: IBAN Attestation Trust Check
    if not iban_attestation.is_valid:
        return PayeeVerificationResult(
            status=VerificationStatus.TRUST_ERROR,
            session_id=session_id,
            reason="IBAN Attestation cryptographic signature is invalid or not trusted.",
        )

    # Rule 4: Subject Binding Check (PID.sub == IBAN.sub)
    if payload.pid.sub != iban_attestation.sub:
        return PayeeVerificationResult(
            status=VerificationStatus.SUBJECT_MISMATCH,
            session_id=session_id,
            actual_pid_name=payload.pid.full_name,
            actual_iban_owner=iban_attestation.account_holder_name or "Unknown",
            reason=(
                f"Subject binding mismatch: PID subject '{payload.pid.sub}' does not match "
                f"IBAN attestation subject '{iban_attestation.sub}'. Suspected account spoofing."
            ),
        )

    # Rule 5: Target IBAN & Payee Name Match
    normalized_expected_iban = _normalize_iban(expected_iban)
    normalized_presented_iban = _normalize_iban(iban_attestation.iban)

    if normalized_expected_iban != normalized_presented_iban:
        return PayeeVerificationResult(
            status=VerificationStatus.IBAN_MISMATCH,
            session_id=session_id,
            reason=(
                f"Presented IBAN '{iban_attestation.iban}' does not match "
                f"expected payment destination IBAN '{expected_iban}'."
            ),
        )

    normalized_expected_name = _normalize_name(expected_payee_name)
    normalized_pid_name = _normalize_name(payload.pid.full_name)
    if normalized_expected_name != normalized_pid_name:
        return PayeeVerificationResult(
            status=VerificationStatus.SUBJECT_MISMATCH,
            session_id=session_id,
            actual_pid_name=payload.pid.full_name,
            actual_iban_owner=iban_attestation.account_holder_name or payload.pid.full_name,
            reason=(
                f"Payee name mismatch: intended recipient was '{expected_payee_name}', "
                f"but legal verified identity from EUDI Wallet is '{payload.pid.full_name}'."
            ),
        )

    # Rule 6: Fully Verified
    return PayeeVerificationResult(
        status=VerificationStatus.VERIFIED,
        session_id=session_id,
        verified_name=payload.pid.full_name,
        verified_iban=normalized_presented_iban,
        issuer_bank=iban_attestation.bank_name or "Demo Issuer Bank",
        reason="EUDI Wallet PID and IBAN ownership attestation cryptographically verified and bound.",
    )
