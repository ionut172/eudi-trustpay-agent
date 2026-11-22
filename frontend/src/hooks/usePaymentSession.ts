import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getHealth,
  presentMockWallet,
  recoverIban,
  sendChat,
  checkPaymentVerification,
  executePayment,
} from '../api/client'
import type { ChatMessage, PaymentSessionState } from '../types/payment'

const createSessionId = () => `demo_${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`

const initialState = (sessionId: string): PaymentSessionState => ({
  session_id: sessionId,
  verification_status: 'NOT_STARTED',
  pending_payment: null,
  verified_payee: null,
})

export function usePaymentSession() {
  const [sessionId, setSessionId] = useState(createSessionId)
  const [adapterMode, setAdapterMode] = useState('MOCK')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [state, setState] = useState<PaymentSessionState>(() => initialState(sessionId))
  const [loading, setLoading] = useState(false)
  const generation = useRef(0)

  useEffect(() => {
    let active = true
    getHealth()
      .then((health) => active && setAdapterMode(health.adapter_mode))
      .catch(() => active && setAdapterMode('OFFLINE'))
    return () => { active = false }
  }, [])

  const addMessage = useCallback((role: ChatMessage['role'], text: string) => {
    setMessages((current) => [...current, { id: crypto.randomUUID(), role, text }])
  }, [])

  const reset = useCallback(() => {
    generation.current += 1
    const nextSessionId = createSessionId()
    setSessionId(nextSessionId)
    setState(initialState(nextSessionId))
    setMessages([])
    setLoading(false)
  }, [])

  const perform = useCallback(async (
    displayText: string,
    action: () => Promise<{ reply: string; state: PaymentSessionState }>,
  ) => {
    const requestGeneration = generation.current
    addMessage('user', displayText)
    setLoading(true)
    try {
      const response = await action()
      if (requestGeneration !== generation.current) return
      setState(response.state)
      addMessage('assistant', response.reply)
    } catch (error) {
      if (requestGeneration !== generation.current) return
      addMessage('assistant', error instanceof Error ? error.message : 'Request failed.')
    } finally {
      if (requestGeneration === generation.current) setLoading(false)
    }
  }, [addMessage])

  const sendMessage = useCallback((message: string) => {
    const text = message.trim()
    if (!text) return
    void perform(text, () => sendChat(sessionId, text))
  }, [perform, sessionId])

  const checkVerification = useCallback(() => {
    void perform('Check verification status', () => checkPaymentVerification(sessionId))
  }, [perform, sessionId])

  const sendPayment = useCallback(() => {
    void perform('Confirm and send payment', () => executePayment(sessionId))
  }, [perform, sessionId])

  const recover = useCallback(async () => {
    const requestGeneration = generation.current
    setLoading(true)
    try {
      await recoverIban(sessionId)
      if (requestGeneration !== generation.current) return
      const response = await checkPaymentVerification(sessionId)
      if (requestGeneration !== generation.current) return
      setState(response.state)
      addMessage('assistant', response.reply)
    } catch (error) {
      if (requestGeneration === generation.current) {
        addMessage('assistant', error instanceof Error ? error.message : 'Recovery failed.')
      }
    } finally {
      if (requestGeneration === generation.current) setLoading(false)
    }
  }, [addMessage, sessionId])

  const presentWallet = useCallback(async (walletId: string) => {
    const requestGeneration = generation.current
    const requestSessionId = sessionId
    setLoading(true)
    try {
      await presentMockWallet(walletId, requestSessionId)
      if (requestGeneration !== generation.current) return
      const response = await checkPaymentVerification(requestSessionId)
      if (requestGeneration !== generation.current) return
      setState(response.state)
      addMessage('assistant', response.reply)
    } catch (error) {
      if (requestGeneration === generation.current) throw error
    } finally {
      if (requestGeneration === generation.current) setLoading(false)
    }
  }, [addMessage, sessionId])

  return {
    sessionId,
    adapterMode,
    messages,
    state,
    loading,
    sendMessage,
    checkVerification,
    sendPayment,
    recover,
    presentWallet,
    reset,
  }
}
