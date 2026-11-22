import { AlertTriangle, BadgeCheck, LockKeyhole, ReceiptText, RotateCcw, Send, ShieldCheck, WalletCards } from 'lucide-react'
import type { PaymentSessionState } from '../types/payment'

interface StateCardsProps {
  state: PaymentSessionState
  loading: boolean
  onCheck: () => void
  onExecute: () => void
  onReset: () => void
  onRecover: () => void
  onWallet: () => void
}

function CardShell({ tone, children }: { tone: 'green' | 'amber' | 'red' | 'blue'; children: React.ReactNode }) {
  return <div className={`state-card state-${tone}`}>{children}</div>
}

export function StateCards({ state, loading, onCheck, onExecute, onReset, onRecover, onWallet }: StateCardsProps) {
  const payment = state.pending_payment

  if (state.verification_status === 'VERIFIED') {
    return (
      <CardShell tone="green">
        <div className="state-title"><span className="state-icon success"><BadgeCheck size={18} /></span><div><small>SECURITY VERDICT</small><strong>Identity verified</strong></div></div>
        <div className="identity-row"><span>Verified legal name</span><strong>{state.verified_payee?.name || '—'}</strong></div>
        <div className="identity-row"><span>Verified IBAN</span><strong className="mono">{state.verified_payee?.iban || '—'}</strong></div>
        <div className="trust-line"><ShieldCheck size={14} /> PID subject bound to bank account</div>
        <button className="primary-action" disabled={loading} onClick={onExecute}><Send size={16} /> Confirm & send payment</button>
      </CardShell>
    )
  }

  if (state.verification_status === 'FAST_PATH_APPROVED') {
    return (
      <CardShell tone="blue">
        <div className="state-title"><span className="state-icon pending"><ShieldCheck size={18} /></span><div><small>TRUSTED FAVORITE</small><strong>Bank details match your saved beneficiary</strong></div></div>
        <p>This route uses the bank’s configured favorite policy. It is not EUDI verification.</p>
        <div className="identity-row"><span>Amount limit</span><strong>€{Number(state.pending_payment?.amount || 0).toFixed(2)}</strong></div>
        <button className="primary-action" disabled={loading} onClick={onExecute}><Send size={16} /> Send payment</button>
      </CardShell>
    )
  }

  if (state.verification_status === 'COMPLETED') {
    return (
      <CardShell tone="green">
        <div className="state-title"><span className="state-icon success"><ReceiptText size={18} /></span><div><small>PAYMENT EXECUTED</small><strong>SEPA Instant completed</strong></div></div>
        <div className="receipt-amount">€{Number(payment?.amount || 0).toFixed(2)}</div>
        <div className="identity-row"><span>Recipient</span><strong>{payment?.creditor_name || '—'}</strong></div>
        <div className="identity-row"><span>Transaction</span><strong className="mono">{state.transaction_id || '—'}</strong></div>
        <button className="secondary-action" onClick={onWallet}><WalletCards size={16} /> View credential in wallet</button>
      </CardShell>
    )
  }

  if (state.verification_status === 'RECOVERY_REQUIRED') {
    return (
      <CardShell tone="amber">
        <div className="state-title"><span className="state-icon warning"><WalletCards size={18} /></span><div><small>RECOVERY REQUIRED</small><strong>IBAN attestation missing</strong></div></div>
        <p>The wallet has a valid PID but no active bank-issued IBAN attestation. In this demo, the recovery action simulates the issuer step.</p>
        <button className="amber-action" disabled={loading} onClick={onRecover}>Simulate attestation issuance</button>
      </CardShell>
    )
  }

  if (['SUBJECT_MISMATCH', 'IBAN_MISMATCH', 'TRUST_ERROR', 'REJECTED'].includes(state.verification_status)) {
    return (
      <CardShell tone="red">
        <div className="state-title"><span className="state-icon danger"><AlertTriangle size={18} /></span><div><small>SECURITY ALERT</small><strong>Payment blocked</strong></div></div>
        <p>The account holder or cryptographic binding does not match the declared payment destination.</p>
        <div className="blocked-lock"><LockKeyhole size={14} /> Execution blocked by security guardrail</div>
        <button className="secondary-action" onClick={onReset}><RotateCcw size={16} /> Start new payment</button>
      </CardShell>
    )
  }

  if (state.verification_status === 'PENDING') {
    return (
      <CardShell tone="amber">
        <div className="state-title"><span className="state-icon pending"><span className="spinner" /></span><div><small>OID4VP REQUEST</small><strong>Awaiting wallet approval</strong></div></div>
        {payment && <><div className="identity-row"><span>Payee</span><strong>{payment.creditor_name}</strong></div><div className="identity-row"><span>IBAN</span><strong className="mono">{payment.creditor_iban}</strong></div><div className="identity-row"><span>Amount</span><strong>€{payment.amount.toFixed(2)}</strong></div></>}
        <button className="secondary-action" disabled={loading} onClick={onCheck}>Check status</button>
      </CardShell>
    )
  }

  if (state.verification_status === 'STEP_UP_REQUIRED') {
    return (
      <CardShell tone="amber">
        <div className="state-title"><span className="state-icon warning"><ShieldCheck size={18} /></span><div><small>ADDITIONAL CHECK REQUIRED</small><strong>Verify this payment with the wallet</strong></div></div>
        <p>This recipient, account, or amount is not covered by the bank’s trusted-recipient settings. Payment stays blocked until EUDI verification succeeds.</p>
      </CardShell>
    )
  }

  return null
}
