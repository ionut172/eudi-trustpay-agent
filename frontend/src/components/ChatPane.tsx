import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Bot, ShieldCheck, UserRound } from 'lucide-react'
import type { ChatMessage, PaymentSessionState } from '../types/payment'
import { StateCards } from './StateCards'

interface ChatPaneProps {
  messages: ChatMessage[]
  state: PaymentSessionState
  loading: boolean
  onSendMessage: (message: string) => void
  onCheck: () => void
  onExecute: () => void
  onReset: () => void
  onRecover: () => void
  onWallet: () => void
}

const examples = [
  'Send €75.50 to Alice Visser',
  'I want to pay George Visser €125',
]

export function ChatPane({ messages, state, loading, onSendMessage, onCheck, onExecute, onReset, onRecover, onWallet }: ChatPaneProps) {
  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, state])

  const submit = () => {
    const message = draft.trim()
    if (!message || loading) return
    onSendMessage(message)
    setDraft('')
  }

  const chooseExample = (message: string) => onSendMessage(message)

  return (
    <section className="chat-pane" aria-label="Payment assistant">
      <div className="chat-content">
          <div className="chat-heading">
            <div><h1>Send a payment</h1><p>Choose a contact or tell me who to pay. We’ll check the account before sending.</p></div>
          <div className="secure-label"><ShieldCheck size={15} /> Protected payments</div>
        </div>

        <div className="quick-chips">
          {examples.map((example) => <button key={example} disabled={loading} onClick={() => chooseExample(example)}>{example}</button>)}
        </div>

        <div className="messages" aria-live="polite" aria-busy={loading}>
          {messages.length === 0 && (
            <div className="empty-chat">
              <div className="empty-icon"><Bot size={24} /></div>
              <h2>How can I help with your payment?</h2>
              <p>Tell me who you want to pay and how much. I’ll ask if I need any details.</p>
            </div>
          )}

          {messages.map((message, index) => (
            <div key={message.id}>
              <div className={`message-row ${message.role}`}>
                {message.role === 'assistant' && <div className="avatar"><Bot size={16} /></div>}
                <div className="message-bubble">
                  {message.text.split('\n').map((line, lineIndex) => <p key={lineIndex}>{line || '\u00A0'}</p>)}
                </div>
                {message.role === 'user' && <div className="avatar user"><UserRound size={15} /></div>}
              </div>
              {message.role === 'assistant' && index === messages.length - 1 && (
                <StateCards state={state} loading={loading} onCheck={onCheck} onExecute={onExecute} onReset={onReset} onRecover={onRecover} onWallet={onWallet} />
              )}
            </div>
          ))}

          {loading && <div className="message-row assistant"><div className="avatar"><Bot size={16} /></div><div className="typing"><i /><i /><i /></div></div>}
          <div ref={endRef} />
        </div>
      </div>

      <form className="composer-wrap" onSubmit={(event) => { event.preventDefault(); submit() }}>
        <div className="chat-composer">
          <textarea
            aria-label="Message TrustPay"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            placeholder="Message TrustPay… e.g. Send €75 to Alice"
            rows={1}
            disabled={loading}
          />
          <button className="send-button" type="submit" disabled={!draft.trim() || loading} aria-label="Send message"><ArrowUp size={18} /></button>
        </div>
        <span className="composer-note">Enter to send · Shift + Enter for a new line</span>
      </form>
    </section>
  )
}
