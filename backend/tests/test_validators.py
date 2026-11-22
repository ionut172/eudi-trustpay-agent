"""Unit tests for EUDI Wallet domain validation security rules."""

import pytest
from app.domain.models import (
    IBANAttestationCredential,
    PIDCredential,
    VerificationStatus,
    WalletPresentationPayload,
)
from app.domain.validators import evaluate_wallet_presentation


@pytest.fixture
def valid_pid() -> PIDCredential:
    return PIDCredential(
        sub="urn:eu:pid:NL-12345678",
        given_name="Alice",
        family_name="Visser",
        birth_date="1992-05-14",
        is_valid=True,
    )


@pytest.fixture
def valid_iban_attestation() -> IBANAttestationCredential:
    return IBANAttestationCredential(
        sub="urn:eu:pid:NL-12345678",
        iban="NL91INGB0001234567",
        account_holder_name="Alice Visser",
        bank_name="Demo Issuer Bank",
        is_valid=True,
        is_expired=False,
    )


def test_happy_path_verified(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Happy Path: PID and IBAN attestation valid, subjects match, target matches -> VERIFIED."""
    payload = WalletPresentationPayload(
        session_id="session-test-happy",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.VERIFIED
    assert result.verified_name == "Alice Visser"
    assert result.verified_iban == "NL91INGB0001234567"
    assert result.issuer_bank == "Demo Issuer Bank"


def test_pid_cryptographic_trust_error(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Rule 1: Invalid PID cryptographic signature -> TRUST_ERROR."""
    valid_pid.is_valid = False
    payload = WalletPresentationPayload(
        session_id="session-test-pid-trust",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.TRUST_ERROR
    assert "PID cryptographic signature is invalid" in (result.reason or "")


def test_recovery_required_missing_iban_attestation(valid_pid: PIDCredential) -> None:
    """Rule 2: Valid PID but missing IBAN attestation -> RECOVERY_REQUIRED."""
    payload = WalletPresentationPayload(
        session_id="session-test-missing-iban",
        pid=valid_pid,
        iban_attestation=None,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.RECOVERY_REQUIRED
    assert result.recovery_issuer_url is not None
    assert "missing or expired" in (result.reason or "").lower()


def test_recovery_required_expired_iban_attestation(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Rule 2: Valid PID but expired IBAN attestation -> RECOVERY_REQUIRED."""
    valid_iban_attestation.is_expired = True
    payload = WalletPresentationPayload(
        session_id="session-test-expired-iban",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.RECOVERY_REQUIRED
    assert result.recovery_issuer_url is not None


def test_iban_attestation_trust_error(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Rule 3: IBAN attestation signature invalid -> TRUST_ERROR."""
    valid_iban_attestation.is_valid = False
    payload = WalletPresentationPayload(
        session_id="session-test-iban-trust",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.TRUST_ERROR
    assert "IBAN Attestation cryptographic signature is invalid" in (result.reason or "")


def test_subject_binding_mismatch(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Rule 4: Subject Binding Check: PID.sub != IBAN.sub -> SUBJECT_MISMATCH (mule account attack)."""
    # IBAN belongs to a different subject than the PID holder
    valid_iban_attestation.sub = "urn:eu:pid:RO-99999999"
    valid_iban_attestation.account_holder_name = "Bob Mule"

    payload = WalletPresentationPayload(
        session_id="session-test-subject-mismatch",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.SUBJECT_MISMATCH
    assert result.actual_pid_name == "Alice Visser"
    assert result.actual_iban_owner == "Bob Mule"
    assert "Subject binding mismatch" in (result.reason or "")


def test_target_iban_mismatch(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Rule 5: Presented IBAN does not match target payment destination -> IBAN_MISMATCH."""
    valid_iban_attestation.iban = "NL99INGB0009999999"
    payload = WalletPresentationPayload(
        session_id="session-test-iban-mismatch",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice Visser",
    )

    assert result.status == VerificationStatus.IBAN_MISMATCH
    assert "does not match expected payment destination IBAN" in (result.reason or "")


def test_expected_name_differs_from_pid_full_name(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Rule 5: Expected payee name differs from verified PID identity -> SUBJECT_MISMATCH."""
    payload = WalletPresentationPayload(
        session_id="session-test-name-mismatch",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Charlie Fraudster",
    )

    assert result.status == VerificationStatus.SUBJECT_MISMATCH
    assert result.actual_pid_name == "Alice Visser"
    assert "Payee name mismatch" in (result.reason or "")


def test_first_name_only_does_not_match_verified_full_name(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    payload = WalletPresentationPayload(
        session_id="session-test-first-name-only",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="Alice",
    )

    assert result.status == VerificationStatus.SUBJECT_MISMATCH
    assert result.actual_pid_name == "Alice Visser"


def test_iban_and_name_normalization_case_and_whitespace(
    valid_pid: PIDCredential,
    valid_iban_attestation: IBANAttestationCredential,
) -> None:
    """Verify spaces and case are normalized properly."""
    valid_iban_attestation.iban = "nl91 ingb 0001 2345 67"
    payload = WalletPresentationPayload(
        session_id="session-test-norm",
        pid=valid_pid,
        iban_attestation=valid_iban_attestation,
    )

    result = evaluate_wallet_presentation(
        payload=payload,
        expected_iban="NL91INGB0001234567",
        expected_payee_name="  alice   visser  ",
    )

    assert result.status == VerificationStatus.VERIFIED
    assert result.verified_iban == "NL91INGB0001234567"
    assert result.verified_name == "Alice Visser"
