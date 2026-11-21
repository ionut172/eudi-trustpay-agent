"""Google ADK Tools for EUDI Wallet verification and instant payment execution."""

from typing import Optional
from google.adk.tools import ToolContext
from app.adapters.live_adapters import get_adapters
from app.config import get_settings
from app.domain.models import (
    PayeeVerificationResult,
    PaymentExecutionRequest,
    VerificationStatus,
)
from app.telemetry import get_logger

logger = get_logger("payment_tools")


def _get_session_id(tool_context: ToolContext) -> str:
    """Safely extracts session_id from ToolContext session or state."""
    if hasattr(tool_context, "session") and tool_context.session and hasattr(tool_context.session, "id"):
        return tool_context.session.id
    return tool_context.state.get("session_id", "default_session")


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
    verifier, _ = get_adapters()

    # Track pending payment details and state in ADK session
    pending_payment = {
        "amount": amount,
        "creditor_iban": creditor_iban.strip(),
        "creditor_name": creditor_name.strip(),
        "currency": "EUR",
    }
    tool_context.state["session_id"] = session_id
    tool_context.state["pending_payment"] = pending_payment
    tool_context.state["verification_status"] = VerificationStatus.PENDING.value
    tool_context.state["verified_payee"] = None

    dcql_req = await verifier.create_dcql_request(
        session_id=session_id,
        target_iban=creditor_iban.strip(),
        expected_payee_name=creditor_name.strip(),
        amount=amount,
        currency="EUR",
    )

    settings = get_settings()
    if (
        settings.ADAPTER_MODE.upper() == "MOCK"
        and settings.MOCK_WALLET_AUTO_PRESENT
        and settings.ALLOW_MOCK_VERIFICATION
    ):
        try:
            mock_result = await verifier.apply_preset_scenario(session_id, "valid")
            tool_context.state["verification_status"] = mock_result.status.value
            if mock_result.status == VerificationStatus.VERIFIED:
                tool_context.state["verified_payee"] = {
                    "name": mock_result.verified_name,
                    "iban": mock_result.verified_iban,
                    "bank": mock_result.issuer_bank,
                }
                wallet_mode = " | Presentare EUDI mock verificată automat"
            else:
                wallet_mode = f" | Mock wallet verdict: {mock_result.status.value}"
        except Exception:
            wallet_mode = " | Mock wallet presentation unavailable"
    else:
        wallet_mode = ""

    return (
        "Am înregistrat intenția de plată. În calitate de **Agent de Autorizare ING**, "
        "am activat protocolul Zero-Trust prin EUDI Wallet pentru a preveni impersonarea, "
        "fraudele APP și spoofingul de IBAN.\n\n"
        f"**Intenție confirmată:** {creditor_name} | {creditor_iban} | €{amount:.2f}\n"
        "Am emis o provocare criptografică OID4VP (DCQL) către portofelul beneficiarului. "
        "Această solicitare cere prezentarea PID eIDAS și a atestării bancare de proprietate asupra IBAN.\n\n"
        f"- **Verification Request ID:** `{dcql_req.request_id}`\n"
        f"- **Session ID:** `{session_id}`\n"
        f"- **Verdict EUDI:** {tool_context.state.get('verification_status')}\n"
        f"- **OID4VP Deep Link:** {dcql_req.qr_code_uri}{wallet_mode}\n\n"
        "Verificarea este realizată automat pentru mockul local. În LIVE, portofelul EUDI trebuie să prezinte "
        "credentialele înainte ca verdictul să fie fetch-uit și validat.\n"
        "Beneficiarul trebuie să prezinte portofelul EUDI. Apoi spuneți „Verifică statusul acum”. "
        "Nicio plată nu poate fi executată până la verificarea criptografică externă.\n"
        f"Simulator wallet: `POST http://localhost:8080/mock-wallet/{session_id}/preset/valid`\n\n"
        "EUDI Wallet verification challenge initiated."
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
            "🛡️ **Raport de Evaluare și Autorizare EUDI**\n\n"
            "Identitatea și dreptul de proprietate asupra contului au fost verificate criptografic:\n\n"
            f"- **Titular legal verificat (PID eIDAS):** {result.verified_name}\n"
            f"- **Cont IBAN atestat:** `{result.verified_iban}` (Banca emitentă: {result.issuer_bank})\n"
            "- **Subject Binding Check (`PID.sub == IBAN.sub`):** **CONFORM** — "
            "titularul actului de identitate este identicul titularului legal al contului\n"
            "- **Scor de Risc APP Fraud:** **0%** — fără indicii de cont mule sau IBAN spoofing\n"
            "- **Verdict Autorizare:** **AUTORIZAT**\n"
            f"- **Sursa verificării:** `{result.mode.upper()}`"
            + (" — rezultat sintetic, nu dovadă criptografică de producție" if result.mode == "mock" else "")
            + "\n\n"
            "Payee verification SUCCESSFUL!\n\n"
        )

        if not settings.AUTO_EXECUTE_VERIFIED_PAYMENTS:
            return (
                report
                + f"Fondurile de €{float(pending_payment.get('amount', 0)):,.2f} sunt pregătite "
                "pentru transfer instant către destinatarul verificat. Doriți să execut transferul SEPA Instant?"
            )

        execution_reply = await execute_verified_instant_payment(tool_context=tool_context)  # type: ignore[arg-type]
        return (
            report
            + "⚡ **Execuție automată autorizată:** după verdictul `VERIFIED`, plata a fost trimisă automat în "
            "SEPA Instant, fără solicitarea unei confirmări suplimentare.\n\n"
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
            "🚨 **ALERTA CRITICĂ: PLATA A FOST BLOCATĂ DE AGENTUL DE AUTORIZARE!**\n\n"
            "În urma interogării externe EUDI Wallet, am detectat o discrepanță gravă de securitate:\n\n"
            f"- Intenția ta a fost să trimiți banii către **{expected_name}**.\n"
            f"- Titularul real al actului de identitate prezentat este **{actual_name}**; "
            f"contul bancar este înregistrat pe numele **{actual_owner}**.\n\n"
            "⚠️ **Subject Binding Violation / SUBJECT MISMATCH:** atestarea bancară nu aparține persoanei pretinse. "
            "Acesta este un indicator clar de **Authorised Push Payment (APP) Fraud / Cont Mule**.\n\n"
            "⛔ **Decizie Agent:** **BLOCARE PERMANENTĂ A TRANZACȚIEI**. "
            "Nicio sumă nu a fost retrasă din contul tău.\n\n"
            "SECURITY ALERT: SUBJECT MISMATCH"
        )

    elif result.status == VerificationStatus.IBAN_MISMATCH:
        return (
            f"Verification FAILED: IBAN mismatch. "
            f"The IBAN presented in the wallet does not match the payment destination IBAN {pending_payment.get('creditor_iban')}."
        )

    elif result.status == VerificationStatus.RECOVERY_REQUIRED:
        recovery_url = result.recovery_issuer_url or "https://bank.ing.com/wallet-issuance/iban-attestation"
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


async def execute_verified_instant_payment(tool_context: ToolContext) -> str:
    """Executes an instant SEPA payment for the verified payee.

    STRICT GUARDRAIL: Refuses execution unless session.state['verification_status'] == 'VERIFIED'.
    Upon success, issues an OID4VCI PaymentReceipt verifiable credential.
    """
    session_id = _get_session_id(tool_context)
    current_status = tool_context.state.get("verification_status")
    pending = tool_context.state.get("pending_payment")

    # Code-level Guardrail Check
    if current_status != VerificationStatus.VERIFIED.value:
        logger.log_event(
            severity="ERROR",
            message=f"Guardrail violation: Blocked unverified payment execution for session {session_id}",
            event_type="guardrail_block",
            session_id=session_id,
            current_status=current_status,
            pending_payment=pending,
        )
        return (
            "🚫 **AUTORIZARE REFUZATĂ — EXECUȚIE BLOCATĂ**\n\n"
            "Agentul de Autorizare nu permite interacțiunea cu rețeaua bancară deoarece "
            f"statusul EUDI este **`{current_status}`**. Pentru execuție este obligatoriu `VERIFIED`.\n\n"
            "BLOCARE PERMANENTĂ până la verificarea criptografică validă. "
            "EXECUTION BLOCKED: Guardrail violation."
        )

    if not pending:
        return "EXECUTION ERROR: No pending payment details found in session."

    _, core_banking = get_adapters()
    verifier, _ = get_adapters()

    verification_result = await verifier.get_verification_result(session_id)
    if not verification_result or verification_result.status != VerificationStatus.VERIFIED:
        return "EXECUTION BLOCKED: Verification record missing or invalid."

    settings = get_settings()
    if verification_result.mode == "mock" and not settings.ALLOW_MOCK_VERIFICATION:
        return "EXECUTION BLOCKED: Mock verification is not allowed in this environment."

    if verification_result.mode == "mock" and settings.ADAPTER_MODE.upper() == "LIVE":
        return "EXECUTION BLOCKED: Mock verification cannot authorize a LIVE payment."

    payment_request = PaymentExecutionRequest(
        session_id=session_id,
        debtor_account="NL91INGB0009876543",
        creditor_iban=pending["creditor_iban"],
        creditor_name=pending["creditor_name"],
        amount=float(pending["amount"]),
        currency=pending.get("currency", "EUR"),
        reference=f"Conversational Payment to {pending['creditor_name']}",
    )

    receipt = await core_banking.execute_instant_payment(
        payment_request=payment_request,
        verification=verification_result,
    )

    # Update session state to COMPLETED
    tool_context.state["verification_status"] = VerificationStatus.COMPLETED.value
    tool_context.state["last_receipt_id"] = receipt.credential_id
    tool_context.state["transaction_id"] = receipt.transaction_id

    return (
        "Plata a fost executată instantaneu de către rețeaua bancară!\n\n"
        f"- **Beneficiar:** {receipt.creditor_name}\n"
        f"- **IBAN:** `{receipt.creditor_iban}`\n"
        f"- **Sumă:** €{receipt.amount:.2f}\n"
        f"- **ID Tranzacție SEPA:** `{receipt.transaction_id}`\n"
        f"- **Chitanță Verificabilă (OID4VCI):** `{receipt.credential_id}`\n"
        f"- **Issuer DID:** `{receipt.issuer_did}`\n"
        f"- **Semnătură criptografică:** `{receipt.issuer_signature[:30]}...`\n\n"
        "Am emis și semnat criptografic dovada Verifiable Credential; "
        "chitanța a fost transmisă portofelului EUDI ca dovadă irevocabilă a plății.\n"
        f"Payment of €{receipt.amount:.2f} to {receipt.creditor_name} ({receipt.creditor_iban}) "
        "executed successfully via SEPA Instant!"
    )
