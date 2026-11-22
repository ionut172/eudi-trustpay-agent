import type { PaymentSessionState } from '../types/payment'

export function WalletStateInspector({ state }: { state: PaymentSessionState }) {
  return (
    <details className="state-inspector">
      <summary>Technical state</summary>
      <pre>{JSON.stringify(state, null, 2)}</pre>
    </details>
  )
}
