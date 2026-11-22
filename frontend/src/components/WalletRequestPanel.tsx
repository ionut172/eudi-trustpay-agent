import { Check, Fingerprint, ShieldCheck, WalletCards } from 'lucide-react'
import type { PendingPayment } from '../types/payment'

interface WalletRequestPanelProps {
  pending: PendingPayment | null
  wallets: string[]
  disabled: boolean
  onPresent: (walletId: string) => void
}

export function WalletRequestPanel({ pending, wallets, disabled, onPresent }: WalletRequestPanelProps) {
  if (!pending) {
    return (
      <div className="wallet-idle">
        <div className="wallet-symbol"><Fingerprint size={22} /></div>
        <h3>No wallet request yet</h3>
        <p>Start a payment to request proof of identity and account ownership.</p>
      </div>
    )
  }

  return (
    <div className="wallet-request-content">
      <section className="request-summary" aria-label="Requested payment details">
        <div className="request-heading"><span className="request-icon"><ShieldCheck size={19} /></span><div><h3>Identity and account proof</h3><p>TrustPay verifier request</p></div></div>
        <dl className="request-details">
          <div><dt>Recipient</dt><dd>{pending.creditor_name}</dd></div>
          <div><dt>Account</dt><dd className="mono">{pending.creditor_iban}</dd></div>
          <div><dt>Amount</dt><dd>€{pending.amount.toFixed(2)} {pending.currency}</dd></div>
        </dl>
        <ul className="claim-list">
          <li><Check size={14} /> Personal identity</li>
          <li><Check size={14} /> IBAN ownership</li>
          <li><Check size={14} /> Issuer trust</li>
        </ul>
      </section>

      <section className="wallet-choice" aria-labelledby="wallet-choice-title">
        <div className="section-heading"><h3 id="wallet-choice-title">Choose a demo wallet</h3><span>{wallets.length} available</span></div>
        <div className="wallet-list">
          {wallets.map((wallet) => (
            <button key={wallet} disabled={disabled} onClick={() => onPresent(wallet)}>
              <span className="wallet-dot"><WalletCards size={16} /></span>
              <span className="wallet-choice-copy"><strong>{wallet.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())}</strong><small>Present wallet credentials</small></span>
              <span className="present-label">Present</span>
            </button>
          ))}
        </div>
        {disabled && <p className="inline-status" role="status">Waiting for the current payment request to finish.</p>}
      </section>
    </div>
  )
}
