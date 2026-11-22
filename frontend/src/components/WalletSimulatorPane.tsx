import { useEffect, useState } from 'react'
import { Fingerprint } from 'lucide-react'
import type { PayeeVerificationResult, PaymentReceipt, PaymentSessionState } from '../types/payment'
import { getReceipts, listMockWallets, presentMockWallet } from '../api/client'
import { WalletRequestPanel } from './WalletRequestPanel'
import { WalletCredentialsPanel } from './WalletCredentialsPanel'

interface WalletProps {
  sessionId: string
  state: PaymentSessionState
  activeTab: 'request' | 'credentials'
  onTabChange: (tab: 'request' | 'credentials') => void
  onVerified: (result: PayeeVerificationResult) => Promise<void>
  busy: boolean
}

export function WalletSimulatorPane({ sessionId, state, activeTab, onTabChange, onVerified, busy }: WalletProps) {
  const [wallets, setWallets] = useState<string[]>([])
  const [receipts, setReceipts] = useState<PaymentReceipt[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    listMockWallets()
      .then((result) => {
        if (!cancelled) setWallets(result.wallets)
      })
      .catch(() => {
        if (!cancelled) setWallets(['alice', 'george', 'george_bcr', 'ionut'])
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getReceipts(sessionId)
      .then((items) => {
        if (!cancelled) setReceipts(items)
      })
      .catch(() => {
        if (!cancelled) setReceipts([])
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, state.verification_status])

  const present = async (walletId: string) => {
    setError('')
    try {
      const result = await presentMockWallet(walletId, sessionId)
      await onVerified(result)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Wallet presentation failed.')
    }
  }

  return (
    <aside className="wallet-pane">
      <section className="wallet-inspector">
        <header className="wallet-header">
          <div className="wallet-avatar"><Fingerprint size={19} /></div>
          <div><h2>EUDI wallet simulator</h2><p>Middleware integration · demo</p></div>
          <span className={`wallet-status status-${state.verification_status.toLowerCase()}`}>{state.verification_status.replaceAll('_', ' ').toLowerCase()}</span>
        </header>
        <div className="wallet-tabs" role="tablist" aria-label="Wallet views">
          <button role="tab" aria-selected={activeTab === 'request'} className={activeTab === 'request' ? 'active' : ''} onClick={() => onTabChange('request')}>Wallet request</button>
          <button role="tab" aria-selected={activeTab === 'credentials'} className={activeTab === 'credentials' ? 'active' : ''} onClick={() => onTabChange('credentials')}>Returned evidence</button>
        </div>
        <p className="middleware-note">See how the payment middleware requests and checks wallet evidence.</p>
        {error && <p className="wallet-error" role="alert">{error}</p>}
        {activeTab === 'request'
          ? <WalletRequestPanel pending={state.pending_payment} wallets={wallets} disabled={busy || !state.pending_payment} onPresent={(walletId) => void present(walletId)} />
          : <WalletCredentialsPanel state={state} receipts={receipts} />}
      </section>
    </aside>
  )
}
