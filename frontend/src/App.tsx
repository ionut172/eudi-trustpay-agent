import { useState } from 'react'
import { BatteryFull, Signal, Wifi } from 'lucide-react'
import { Header } from './components/Header'
import { ChatPane } from './components/ChatPane'
import { RecipientsPanel } from './components/RecipientsPanel'
import { WalletSimulatorPane } from './components/WalletSimulatorPane'
import { usePaymentSession } from './hooks/usePaymentSession'

export default function App() {
  const payment = usePaymentSession()
  const [walletTab, setWalletTab] = useState<'request' | 'credentials'>('request')

  return (
    <div className="app-shell">
      <main className="mobile-demo-stage">
        <div className="phone-preview-column">
          <p className="phone-preview-label">Customer experience · mobile banking prototype</p>
          <section className="phone-frame" aria-label="Mobile banking app preview">
            <div className="phone-screen">
              <div className="phone-status-bar" aria-hidden="true">
                <span>9:41</span>
                <span className="phone-status-icons"><Signal size={14} /><Wifi size={14} /><BatteryFull size={16} /></span>
              </div>
              <Header sessionId={payment.sessionId} adapterMode={payment.adapterMode} onReset={() => { payment.reset(); setWalletTab('request') }} />
              <RecipientsPanel onSelect={(name) => payment.sendMessage(`I want to pay ${name}`)} />
              <ChatPane
                messages={payment.messages}
                state={payment.state}
                loading={payment.loading}
                onSendMessage={payment.sendMessage}
                onCheck={payment.checkVerification}
                onExecute={payment.sendPayment}
                onReset={() => { payment.reset(); setWalletTab('request') }}
                onRecover={payment.recover}
                onWallet={() => setWalletTab('credentials')}
              />
              <div className="phone-home-indicator" aria-hidden="true"><span /></div>
            </div>
          </section>
        </div>

        <section className="middleware-sidecar" aria-label="Middleware integration demo">
          <header className="middleware-heading">
            <span className="middleware-heading-icon"><Signal size={17} /></span>
            <div><h1>Agentic payment middleware</h1><p>Backend verification flow demonstrated alongside the customer experience.</p></div>
            <span className={`mode-pill mode-${payment.adapterMode.toLowerCase()}`}><i /> {payment.adapterMode || 'MOCK'}</span>
          </header>
          <WalletSimulatorPane
            sessionId={payment.sessionId}
            state={payment.state}
            activeTab={walletTab}
            onTabChange={setWalletTab}
            onPresentWallet={payment.presentWallet}
            busy={payment.loading}
          />
        </section>
      </main>
    </div>
  )
}
