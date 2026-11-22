import { FileCheck2, Fingerprint, WalletCards } from 'lucide-react'
import type { PaymentReceipt, PaymentSessionState } from '../types/payment'
import { WalletStateInspector } from './WalletStateInspector'

interface WalletCredentialsPanelProps {
  state: PaymentSessionState
  receipts: PaymentReceipt[]
}

export function WalletCredentialsPanel({ state, receipts }: WalletCredentialsPanelProps) {
  return (
    <div className="credential-content">
      <section className="credential-record">
        <div className="credential-symbol"><Fingerprint size={17} /></div>
        <div><h3>Personal identity</h3><strong>{state.verified_payee?.name || 'No verified identity'}</strong><p>European Person Identification Data</p></div>
      </section>
      <section className="credential-record">
        <div className="credential-symbol"><WalletCards size={17} /></div>
        <div><h3>Account ownership</h3><strong className="mono">{state.verified_payee?.iban || 'No verified account'}</strong><p>{state.verified_payee?.bank || 'Waiting for verification'}</p></div>
      </section>
      {receipts.map((receipt) => (
        <section className="credential-record receipt" key={receipt.credential_id}>
          <div className="credential-symbol"><FileCheck2 size={17} /></div>
          <div><h3>Payment receipt</h3><strong>{receipt.transaction_id}</strong><p>{receipt.credential_id}</p></div>
        </section>
      ))}
      {receipts.length === 0 && state.verification_status === 'COMPLETED' && <p className="inline-status">Receipt is not available from the wallet service yet.</p>}
      <WalletStateInspector state={state} />
    </div>
  )
}
