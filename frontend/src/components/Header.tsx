import { RotateCcw, ShieldCheck } from 'lucide-react'

interface HeaderProps {
  sessionId: string
  adapterMode: string
  onReset: () => void
}

export function Header({ sessionId, adapterMode, onReset }: HeaderProps) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark"><ShieldCheck size={19} /></div>
        <div>
          <strong>Digital Banking</strong>
          <span>Conversational payments · prototype</span>
        </div>
      </div>

      <div className="header-actions">
        <div className="session-pill"><span>Session</span><b>{sessionId}</b></div>
        <div className={`mode-pill mode-${adapterMode.toLowerCase()}`}><i /> {adapterMode || 'MOCK'}</div>
        <button className="icon-button" aria-label="Reset demo" title="Reset demo" onClick={onReset}>
          <RotateCcw size={17} />
        </button>
      </div>
    </header>
  )
}
