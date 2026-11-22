import type {
  ChatResponse,
  PayeeVerificationResult,
  PaymentIntent,
  PaymentReceipt,
} from '../types/payment'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '')
  || 'http://localhost:8080'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const body = await response.text()
    let message = body || `Request failed (${response.status})`
    try {
      const parsed = JSON.parse(body) as { detail?: string }
      message = parsed.detail || message
    } catch {
      // Keep plain-text API errors readable.
    }
    throw new Error(message)
  }

  return response.json() as Promise<T>
}

export async function getHealth() {
  return request<{ status: string; adapter_mode: string }>('/healthz')
}

export async function sendChat(sessionId: string, message: string): Promise<ChatResponse> {
  return request<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, user_id: 'demo-user', message }),
  })
}

export async function sendPaymentIntent(sessionId: string, intent: PaymentIntent): Promise<ChatResponse> {
  return request<ChatResponse>('/api/payment-intents', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, user_id: 'demo-user', intent }),
  })
}

export async function checkPaymentVerification(sessionId: string): Promise<ChatResponse> {
  return request<ChatResponse>(`/api/payments/${encodeURIComponent(sessionId)}/verification?user_id=demo-user`, {
    method: 'POST',
  })
}

export async function executePayment(sessionId: string): Promise<ChatResponse> {
  return request<ChatResponse>(`/api/payments/${encodeURIComponent(sessionId)}/execute?user_id=demo-user`, {
    method: 'POST',
  })
}

export async function applyWalletPreset(sessionId: string, scenario: 'valid' | 'mismatch' | 'missing_iban') {
  return request<PayeeVerificationResult>(`/mock-wallet/${encodeURIComponent(sessionId)}/preset/${scenario}`, {
    method: 'POST',
  })
}

export async function recoverIban(sessionId: string) {
  return request<PayeeVerificationResult>(`/mock-wallet/${encodeURIComponent(sessionId)}/recover-iban`, {
    method: 'POST',
  })
}

export async function getReceipts(sessionId: string) {
  return request<PaymentReceipt[]>(`/mock-wallet/${encodeURIComponent(sessionId)}/receipts`)
}

export async function presentMockWallet(walletId: string, sessionId: string) {
  return request<PayeeVerificationResult>(`/mock-eu-wallet/${encodeURIComponent(walletId)}/present/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
  })
}

export async function listMockWallets() {
  return request<{ wallets: string[] }>('/mock-eu-wallet/wallets')
}
