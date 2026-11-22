"""Google ADK tools and deterministic payment authorization services."""

import datetime
import uuid
from typing import Any, MutableMapping, Optional
from google.adk.tools import ToolContext
from app.adapters.mock_wallet_api import MockEUWalletApi
from app.adapters.live_adapters import get_adapters
from app.config import Settings, get_settings
from app.domain.models import (
    PayeeVerificationResult,
    PaymentAuthorization,
    PaymentExecutionRequest,
    PaymentIntent,
    PaymentRiskAssessment,
    VerificationStatus,
)
from app.telemetry import get_logger

logger = get_logger("payment_tools")


def _get_session_id(tool_context: ToolContext) -> str:
    """Safely extracts session_id from ToolContext session or state."""
    if hasattr(tool_context, "session") and tool_context.session and hasattr(tool_context.session, "id"):
        return tool_context.session.id
    return tool_context.state.get("session_id", "default_session")


def assess_payment_risk(
    intent: PaymentIntent,
    trusted_favorites: dict[str, str],
    max_amount: float,
) -> PaymentRiskAssessment:
    """Apply server-side favorite policy; user or LLM claims cannot authorize payment."""
    name = " ".join((intent.recipient_name or "").strip().casefold().split())
    iban = "".join((intent.recipient_iban or "").upper().split())
    if not name or intent.amount is None:
        return PaymentRiskAssessment(
            route="CLARIFY",
            recipient_name=intent.recipient_name,
            recipient_iban=intent.recipient_iban,
            amount=intent.amount,
            currency=intent.currency,
            reason="Recipient and positive amount are required.",
        )

    favorite_iban = next(
        (
            value
            for favorite_name, value in trusted_favorites.items()
            if " ".join(favorite_name.strip().casefold().split()) == name
        ),
        None,
    )
    expected_iban = "".join((favorite_iban or "").upper().split())
    if favorite_iban and iban == expected_iban and intent.amount <= max_amount:
        return PaymentRiskAssessment(
            route="FAST_PATH_APPROVED",
            recipient_name=intent.recipient_name,
            recipient_iban=expected_iban,
            amount=intent.amount,
            currency=intent.currency,
            reason="Server-side trusted favorite matched exact IBAN and amount limit.",
        )

    return PaymentRiskAssessment(
        route="STEP_UP_REQUIRED",
        recipient_name=intent.recipient_name,
        recipient_iban=iban or None,
        amount=intent.amount,
        currency=intent.currency,
        reason="Recipient is new, IBAN differs, or amount exceeds favorite limit.",
    )


async def initiate_payment_intent(
    intent: PaymentIntent,
    session_id: str,
    state: MutableMapping[str, Any],
    settings: Optional[Settings] = None,
) -> str:
    """Resolve a typed intent, enforce risk policy, then verify or execute."""
    settings = settings or get_settings()
    name = " ".join((intent.recipient_name or "").split())
    if not name:
        return "Who would you like to pay? Please provide the recipient's name."
    if intent.amount is None or intent.amount <= 0:
        state["requested_payee"] = name
        return f"What amount would you like to send to **{name}**?"

    iban = "".join((intent.recipient_iban or "").upper().split())
    if not iban:
        favorite_iban = next(
            (
                value
                for favorite_name, value in settings.TRUSTED_FAVORITES.items()
                if " ".join(favorite_name.strip().casefold().split())
                == " ".join(name.casefold().split())
            ),
            None,
        )
        if favorite_iban:
            iban = "".join(favorite_iban.upper().split())
        elif settings.ADAPTER_MODE.upper() == "MOCK":
            try:
                wallet = MockEUWalletApi().find_wallet_by_name(name)
                iban = wallet.get("account_iban") or wallet["iban_attestation"].get("iban") or ""
                name = " ".join((wallet["pid"]["given_name"], wallet["pid"]["family_name"]))
            except (ValueError, KeyError):
                return f"I couldn't find a unique recipient named **{name}**. Please provide the full name."
        if not iban:
            return f"I couldn't find an account for **{name}**. EUDI verification requires an IBAN known to the bank."

    normalized_intent = PaymentIntent(
        recipient_name=name,
        recipient_iban=iban,
        amount=intent.amount,
        currency=intent.currency,
    )
    risk = assess_payment_risk(
        normalized_intent,
        settings.TRUSTED_FAVORITES,
        settings.FAVORITE_FAST_PATH_MAX_AMOUNT,
    )
    state["session_id"] = session_id
    payment_id = uuid.uuid4().hex
    state.pop("payment_authorization", None)
    state.pop("last_receipt_id", None)
    state.pop("transaction_id", None)
    state["verified_payee"] = None
    state["pending_payment"] = {
        "payment_id": payment_id,
        "amount": intent.amount,
        "creditor_iban": iban,
        "creditor_name": name,
        "currency": intent.currency,
    }

    if risk.route == "FAST_PATH_APPROVED":
        authorization = PaymentAuthorization(
            decision="TRUSTED_FAVORITE",
            session_id=session_id,
            payment_id=payment_id,
            recipient_name=name,
            recipient_iban=iban,
            amount=intent.amount,
            currency=intent.currency,
            favorite_name=name,
            max_amount=settings.FAVORITE_FAST_PATH_MAX_AMOUNT,
            reason=risk.reason,
        )
        state["verification_status"] = VerificationStatus.FAST_PATH_APPROVED.value
        state["payment_authorization"] = authorization.model_dump(mode="json")
        if settings.AUTO_EXECUTE_VERIFIED_PAYMENTS:
            return await _execute_payment_for_state(session_id, state, settings)
        return f"Trusted recipient confirmed. The €{intent.amount:.2f} payment is ready for authorization."

    verifier, _ = get_adapters(settings)
    state["verification_status"] = VerificationStatus.PENDING.value
    state["verified_payee"] = None
    await verifier.create_dcql_request(
        session_id=session_id,
        target_iban=iban,
        expected_payee_name=name,
        amount=intent.amount,
        currency=intent.currency,
    )

    if (
        settings.ADAPTER_MODE.upper() == "MOCK"
        and settings.MOCK_WALLET_AUTO_PRESENT
        and settings.ALLOW_MOCK_VERIFICATION
    ):
        result = await verifier.apply_preset_scenario(session_id, "valid")
        state["verification_status"] = result.status.value
        if result.status == VerificationStatus.VERIFIED:
            state["verified_payee"] = {
                "name": result.verified_name,
                "iban": result.verified_iban,
                "bank": result.issuer_bank,
            }
            if settings.AUTO_EXECUTE_VERIFIED_PAYMENTS:
                return await _execute_payment_for_state(session_id, state, settings)
        return f"Mock wallet verdict: {result.status.value}. Payment remains blocked until authorization."

    return (
        f"EUDI verification has been started for **{name}** for €{intent.amount:.2f}. "
        "The payment will be executed only after the verifier returns a verdict."
    )


async def initiate_payee_eudi_verification(
    amount: float,
    creditor_iban: str,
    creditor_name: str,
    tool_context: ToolContext,
) -> str:
    """Initiates OID4VP EUDI Wallet verification for the intended payment recipient.

    Use this tool when the user wants to make a payment and has specified an amount,
    payee name, and destination IBAN. It creates an OID4VP DCQL request and generates
    a verification challenge to prevent APP fraud and IBAN spoofing.

    Args:
        amount: Payment amount in EUR.
        creditor_iban: Destination International Bank Account Number (IBAN).
        creditor_name: Name of the intended recipient as declared by user.
        tool_context: ADK session context.
    """
    session_id = _get_session_id(tool_context)
    return await initiate_payment_intent(
        PaymentIntent(
            recipient_name=creditor_name,
            recipient_iban=creditor_iban,
            amount=amount,
        ),
        session_id,
        tool_context.state,
    )


async def check_payee_eudi_verification(tool_context: ToolContext) -> str:
    """Checks the verification status of the payee's EUDI Wallet presentation.

    Use this tool to verify whether the payee has completed their OID4VP presentation
    and whether their PID and bank IBAN ownership attestation have been verified.
    """
    session_id = _get_session_id(tool_context)
    pending_payment = tool_context.state.get("pending_payment")

    if not pending_payment:
        return "No pending payment found in session. Please initiate a payment first."

    verifier, _ = get_adapters()
    result: Optional[PayeeVerificationResult] = await verifier.get_verification_result(session_id)

    if not result:
        tool_context.state["verification_status"] = VerificationStatus.PENDING.value
        return (
            "Verification is still PENDING. The payee has not yet presented their EUDI Wallet credentials. "
            "Please ask them to scan the verification QR code or approve the OID4VP request."
        )

    request = await verifier.get_dcql_request(session_id)
    if (
        request is None
        or result.session_id != session_id
        or result.request_id != request.request_id
        or request.target_iban.replace(" ", "").upper()
        != pending_payment.get("creditor_iban", "").replace(" ", "").upper()
        or request.expected_payee_name.casefold()
        != pending_payment.get("creditor_name", "").casefold()
        or request.amount != float(pending_payment.get("amount", 0))
        or request.currency != pending_payment.get("currency", "EUR")
    ):
        tool_context.state["verification_status"] = VerificationStatus.PENDING.value
        return "Verification is not bound to the active payment request. No payment was executed."

    # Synchronize ADK session state with domain validation result
    tool_context.state["verification_status"] = result.status.value

    if result.status == VerificationStatus.VERIFIED:
        tool_context.state["verified_payee"] = {
            "name": result.verified_name,
            "iban": result.verified_iban,
            "bank": result.issuer_bank,
        }

        settings = get_settings()
        report = (
            "🛡️ **EUDI Verification and Authorization Report**\n\n"
            "Identity and account ownership were cryptographically verified:\n\n"
            f"- **Verified legal name (eIDAS PID):** {result.verified_name}\n"
            f"- **Attested IBAN:** `{result.verified_iban}` (Issuing bank: {result.issuer_bank})\n"
            "- **Subject Binding Check (`PID.sub == IBAN.sub`):** **CONFORM** — "
            "the identity-document holder matches the legal account holder\n"
            "- **APP fraud risk score:** **0%** — no indicators of a money-mule account or IBAN spoofing\n"
            "- **Authorization verdict:** **AUTHORIZED**\n"
            f"- **Verification source:** `{result.mode.upper()}`"
            + (" — synthetic result, not production cryptographic evidence" if result.mode == "mock" else "")
            + "\n\n"
            "Payee verification SUCCESSFUL!\n\n"
        )

        if not settings.AUTO_EXECUTE_VERIFIED_PAYMENTS:
            return (
                report
                + f"The funds of €{float(pending_payment.get('amount', 0)):,.2f} are ready "
                "for an instant transfer to the verified recipient. Would you like to execute the SEPA Instant transfer?"
            )

        execution_reply = await execute_verified_instant_payment(tool_context=tool_context)  # type: ignore[arg-type]
        return (
            report
            + "⚡ **Automatic execution authorized:** following the `VERIFIED` verdict, the payment was sent via "
            "SEPA Instant without requesting additional confirmation.\n\n"
            + execution_reply
        )

    elif result.status in (VerificationStatus.SUBJECT_MISMATCH, VerificationStatus.REJECTED):
        expected_name = pending_payment.get("creditor_name")
        actual_name = result.actual_pid_name or "Unknown"
        actual_owner = result.actual_iban_owner or "Unknown"

        logger.log_event(
            severity="WARNING",
            message=f"Fraud alert: Subject mismatch for session {session_id}. Expected: {expected_name}, Got: {actual_name}",
            event_type="guardrail_block",
            session_id=session_id,
            expected_name=expected_name,
            actual_pid_name=actual_name,
            actual_iban_owner=actual_owner,
        )

        return (
            "🚨 **CRITICAL ALERT: PAYMENT BLOCKED BY THE AUTHORIZATION AGENT!**\n\n"
            "The external EUDI Wallet check detected a serious security discrepancy:\n\n"
            f"- You intended to send money to **{expected_name}**.\n"
            f"- The actual holder of the presented identity document is **{actual_name}**; "
            f"the bank account is registered to **{actual_owner}**.\n\n"
            "⚠️ **Subject Binding Violation / SUBJECT MISMATCH:** the bank attestation does not belong to the claimed person. "
            "This is a clear indicator of **Authorised Push Payment (APP) fraud / money-mule activity**.\n\n"
            "⛔ **Agent decision:** **PERMANENT PAYMENT BLOCK**. "
            "No funds have been withdrawn from your account.\n\n"
            "SECURITY ALERT: SUBJECT MISMATCH"
        )

    elif result.status == VerificationStatus.IBAN_MISMATCH:
        return (
            f"Verification FAILED: IBAN mismatch. "
            f"The IBAN presented in the wallet does not match the payment destination IBAN {pending_payment.get('creditor_iban')}."
        )

    elif result.status == VerificationStatus.RECOVERY_REQUIRED:
        recovery_url = result.recovery_issuer_url or "https://issuer.example.com/attestations/iban"
        if result.mode == "mock":
            return (
                "Verification is incomplete: the payee has a valid EUDI PID, but the wallet is missing an active "
                "IBAN account-ownership attestation. This demo simulates the bank issuer's recovery step. "
                "After the attestation is issued, check verification again."
            )
        return (
            f"Verification INCOMPLETE (RECOVERY REQUIRED):\n"
            f"The payee presented a valid EUDI PID, but their wallet is missing an active IBAN Account Ownership Attestation.\n"
            f"To complete verification, the payee must issue their IBAN attestation from their bank at: {recovery_url}.\n"
            f"Once issued, verification can be re-checked."
        )

    elif result.status == VerificationStatus.TRUST_ERROR:
        return (
            f"Verification FAILED: Cryptographic trust error. "
            f"The presented credentials could not be validated against trusted trust anchors ({result.reason})."
        )

    return f"Verification status: {result.status.value}. Reason: {result.reason}"


async def check_payment_verification(
    session_id: str,
    state: MutableMapping[str, Any],
    settings: Optional[Settings] = None,
) -> str:
    """Poll verifier through backend service without constructing a fake ADK context."""
    settings = settings or get_settings()
    pending = state.get("pending_payment")
    if not pending:
        return "No pending payment found in session. Please initiate a payment first."
    if state.get("verification_status") == VerificationStatus.COMPLETED.value:
        return await _execute_payment_for_state(session_id, state, settings)

    verifier, _ = get_adapters(settings)
    result = await verifier.get_verification_result(session_id)
    if result is None:
        state["verification_status"] = VerificationStatus.PENDING.value
        return "Verification is still PENDING. The payee has not yet presented EUDI credentials."

    request = await verifier.get_dcql_request(session_id)
    if (
        request is None
        or result.session_id != session_id
        or result.request_id != request.request_id
        or request.target_iban.replace(" ", "").upper()
        != pending.get("creditor_iban", "").replace(" ", "").upper()
        or request.expected_payee_name.casefold() != pending.get("creditor_name", "").casefold()
        or request.amount != float(pending.get("amount", 0))
        or request.currency != pending.get("currency", "EUR")
    ):
        state["verification_status"] = VerificationStatus.PENDING.value
        return "Verification is not bound to the active payment request. No payment was executed."

    state["verification_status"] = result.status.value
    if result.status == VerificationStatus.VERIFIED:
        state["verified_payee"] = {
            "name": result.verified_name,
            "iban": result.verified_iban,
            "bank": result.issuer_bank,
        }
        if settings.AUTO_EXECUTE_VERIFIED_PAYMENTS:
            execution_reply = await _execute_payment_for_state(session_id, state, settings)
            return (
                "Payee verification SUCCESSFUL\n\n"
                "Automatic execution authorized after the VERIFIED verdict.\n\n"
                + execution_reply
            )
        return "Payee verification SUCCESSFUL. Payment awaits explicit execution."
    if result.status in (VerificationStatus.REJECTED, VerificationStatus.SUBJECT_MISMATCH):
        return (
            "SECURITY ALERT: SUBJECT MISMATCH\n"
            f"Expected: {pending.get('creditor_name')}; presented holder: "
            f"{result.actual_pid_name or 'Unknown'} / {result.actual_iban_owner or 'Unknown'}.\n"
            "PERMANENT PAYMENT BLOCK."
        )
    if result.status == VerificationStatus.RECOVERY_REQUIRED:
        if result.mode == "mock":
            return (
                "Verification is incomplete: the payee has a valid EUDI PID, but the wallet is missing an active "
                "IBAN account-ownership attestation. This demo simulates the bank issuer's recovery step. "
                "After the attestation is issued, check verification again."
            )
        recovery_url = result.recovery_issuer_url or settings.RECOVERY_ISSUER_URL
        return (
            "Verification is incomplete: the payee has a valid EUDI PID, but the wallet is missing an active "
            f"IBAN account-ownership attestation. The payee must request one from their issuing bank: {recovery_url}. "
            "After it is issued, check verification again."
        )
    return f"Verification status: {result.status.value}. Reason: {result.reason}"


async def _execute_payment_for_state(
    session_id: str,
    state: MutableMapping[str, Any],
    settings: Optional[Settings] = None,
) -> str:
    """Execute only after backend revalidates exact payment authorization."""
    settings = settings or get_settings()
    current_status = state.get("verification_status")
    pending = state.get("pending_payment")
    if current_status == VerificationStatus.COMPLETED.value:
        transaction_id = state.get("transaction_id")
        return (
            f"Payment was already completed. Transaction ID: `{transaction_id}`. "
            "No additional transfer was created."
        )
    if current_status not in (VerificationStatus.VERIFIED.value, VerificationStatus.FAST_PATH_APPROVED.value):
        logger.log_event(
            severity="ERROR",
            message=f"Guardrail violation: Blocked unverified payment execution for session {session_id}",
            event_type="guardrail_block",
            session_id=session_id,
            current_status=current_status,
            pending_payment=pending,
        )
        return (
            "🚫 **AUTHORIZATION REFUSED — EXECUTION BLOCKED**\n\n"
            "The Authorization Agent cannot access the banking network because "
            f"the EUDI status is **`{current_status}`**. A `VERIFIED` status is required for execution.\n\n"
            "PERMANENT BLOCK until valid cryptographic verification is completed. "
            "EXECUTION BLOCKED: Guardrail violation."
        )

    if not pending:
        return "EXECUTION ERROR: No pending payment details found in session."
    payment_id = pending.get("payment_id")
    if not isinstance(payment_id, str) or not payment_id:
        return "EXECUTION BLOCKED: Payment identifier is missing."

    verification_result: Optional[PayeeVerificationResult] = None
    if current_status == VerificationStatus.FAST_PATH_APPROVED.value:
        stored = state.get("payment_authorization") or {}
        intent = PaymentIntent(
            recipient_name=pending.get("creditor_name"),
            recipient_iban=pending.get("creditor_iban"),
            amount=float(pending.get("amount", 0)),
            currency=pending.get("currency", "EUR"),
        )
        risk = assess_payment_risk(
            intent,
            settings.TRUSTED_FAVORITES,
            settings.FAVORITE_FAST_PATH_MAX_AMOUNT,
        )
        if (
            risk.route != "FAST_PATH_APPROVED"
            or stored.get("decision") != "TRUSTED_FAVORITE"
            or stored.get("session_id") != session_id
            or stored.get("payment_id") != payment_id
            or stored.get("recipient_iban") != risk.recipient_iban
            or stored.get("recipient_name", "").casefold() != (pending.get("creditor_name") or "").casefold()
            or stored.get("favorite_name", "").casefold() != (pending.get("creditor_name") or "").casefold()
            or float(stored.get("amount", 0)) != intent.amount
            or float(stored.get("max_amount", 0)) != settings.FAVORITE_FAST_PATH_MAX_AMOUNT
            or stored.get("currency", "EUR") != intent.currency
        ):
            return "EXECUTION BLOCKED: Trusted-favorite authorization is missing, stale, or mismatched."
        authorization = PaymentAuthorization.model_validate(stored)
    else:
        verifier, _ = get_adapters(settings)
        verification_result = await verifier.get_verification_result(session_id)
        if not verification_result or verification_result.status != VerificationStatus.VERIFIED:
            return "EXECUTION BLOCKED: Verification record missing or invalid."
        if verification_result.session_id != session_id:
            return "EXECUTION BLOCKED: Verification belongs to a different session."
        dcql_request = await verifier.get_dcql_request(session_id)
        if (
            dcql_request is None
            or dcql_request.session_id != session_id
            or dcql_request.currency != pending.get("currency", "EUR")
            or dcql_request.target_iban.replace(" ", "").upper()
            != pending["creditor_iban"].replace(" ", "").upper()
            or dcql_request.amount != float(pending["amount"])
            or " ".join(dcql_request.expected_payee_name.casefold().split())
            != " ".join(pending["creditor_name"].casefold().split())
        ):
            return "EXECUTION BLOCKED: Verification request does not match pending payment."
        if verification_result.verified_at is None:
            return "EXECUTION BLOCKED: Verification timestamp is missing."
        now = datetime.datetime.now(datetime.timezone.utc)
        verified_at = verification_result.verified_at
        if verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=datetime.timezone.utc)
        age_seconds = (now - verified_at).total_seconds()
        if age_seconds < 0 or age_seconds > settings.VERIFICATION_MAX_AGE_SECONDS:
            return "EXECUTION BLOCKED: Verification result is stale."
        if not verification_result.verification_id:
            return "EXECUTION BLOCKED: Verification identifier is missing."
        if verification_result.request_id != dcql_request.request_id:
            return "EXECUTION BLOCKED: Verification result is not bound to the active request."
        if (
            verification_result.verified_iban or ""
        ).replace(" ", "").upper() != pending["creditor_iban"].replace(" ", "").upper():
            return "EXECUTION BLOCKED: Verified IBAN does not match pending payment."
        if verification_result.mode == "mock" and not settings.ALLOW_MOCK_VERIFICATION:
            return "EXECUTION BLOCKED: Mock verification is not allowed in this environment."
        if verification_result.mode == "mock" and settings.ADAPTER_MODE.upper() == "LIVE":
            return "EXECUTION BLOCKED: Mock verification cannot authorize a LIVE payment."
        authorization = PaymentAuthorization(
            decision="EUDI_VERIFIED",
            session_id=session_id,
            payment_id=payment_id,
            recipient_name=pending["creditor_name"],
            recipient_iban=pending["creditor_iban"],
            amount=float(pending["amount"]),
            currency=pending.get("currency", "EUR"),
            verification_id=verification_result.verification_id,
            reason="External verifier returned VERIFIED for the pending payment.",
        )

    payment_request = PaymentExecutionRequest(
        session_id=session_id,
        payment_id=payment_id,
        debtor_account="NL91INGB0009876543",
        creditor_iban=pending["creditor_iban"],
        creditor_name=pending["creditor_name"],
        amount=float(pending["amount"]),
        currency=pending.get("currency", "EUR"),
        reference=f"Conversational Payment to {pending['creditor_name']}",
    )
    _, core_banking = get_adapters(settings)
    receipt = await core_banking.execute_instant_payment(
        payment_request=payment_request,
        authorization=authorization,
        verification=verification_result,
    )

    state["verification_status"] = VerificationStatus.COMPLETED.value
    state["last_receipt_id"] = receipt.credential_id
    state["transaction_id"] = receipt.transaction_id
    return (
        "Payment was executed instantly by the banking network!\n\n"
        f"- **Recipient:** {receipt.creditor_name}\n"
        f"- **IBAN:** `{receipt.creditor_iban}`\n"
        f"- **Amount:** €{receipt.amount:.2f}\n"
        f"- **SEPA transaction ID:** `{receipt.transaction_id}`\n"
        f"- **Verifiable receipt (OID4VCI):** `{receipt.credential_id}`\n"
        f"- **Issuer DID:** `{receipt.issuer_did}`\n"
        f"- **Cryptographic signature:** `{receipt.issuer_signature[:30]}...`\n\n"
        "A Verifiable Credential payment receipt was issued and cryptographically signed; "
        "it was sent to the EUDI Wallet as an irrevocable proof of payment.\n"
        f"Payment of €{receipt.amount:.2f} to {receipt.creditor_name} ({receipt.creditor_iban}) "
        "executed successfully via SEPA Instant!"
    )


async def execute_verified_instant_payment(tool_context: ToolContext) -> str:
    """Execute only when backend state has valid EUDI or favorite authorization."""
    return await _execute_payment_for_state(
        _get_session_id(tool_context),
        tool_context.state,
    )


async def evaluate_payment_risk(
    recipient_name: Optional[str],
    recipient_iban: Optional[str],
    amount: Optional[float],
    tool_context: ToolContext,
) -> str:
    """Evaluates server-configured favorites; LLM output cannot create favorites."""
    settings = get_settings()
    risk = assess_payment_risk(
        PaymentIntent(
            recipient_name=recipient_name,
            recipient_iban=recipient_iban,
            amount=amount,
        ),
        settings.TRUSTED_FAVORITES,
        settings.FAVORITE_FAST_PATH_MAX_AMOUNT,
    )
    if risk.route == "CLARIFY":
        return f"CLARIFY: {risk.reason}"

    session_id = _get_session_id(tool_context)
    tool_context.state["session_id"] = session_id
    tool_context.state["pending_payment"] = {
        "payment_id": uuid.uuid4().hex,
        "amount": amount,
        "creditor_iban": risk.recipient_iban,
        "creditor_name": recipient_name,
        "currency": "EUR",
    }
    tool_context.state["verification_status"] = risk.route
    if risk.route == "FAST_PATH_APPROVED":
        tool_context.state["payment_authorization"] = PaymentAuthorization(
            decision="TRUSTED_FAVORITE",
            session_id=session_id,
            payment_id=tool_context.state["pending_payment"]["payment_id"],
            recipient_name=recipient_name or "",
            recipient_iban=risk.recipient_iban or "",
            amount=amount or 0,
            favorite_name=recipient_name,
            max_amount=settings.FAVORITE_FAST_PATH_MAX_AMOUNT,
            reason=risk.reason,
        ).model_dump(mode="json")
    return risk.route
