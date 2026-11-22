export type VerificationStatus =
  | 'NOT_STARTED'
  | 'PENDING'
  | 'VERIFIED'
  | 'REJECTED'
  | 'SUBJECT_MISMATCH'
  | 'IBAN_MISMATCH'
  | 'TRUST_ERROR'
  | 'RECOVERY_REQUIRED'
  | 'COMPLETED'
  | 'FAST_PATH_APPROVED'
  | 'STEP_UP_REQUIRED'

export interface PendingPayment {
  amount: number
  currency: string
  creditor_iban: string
  creditor_name: string
  reference?: string
}

export interface PaymentSessionState {
  session_id: string
  verification_status: VerificationStatus
  pending_payment: PendingPayment | null
  verified_payee: {
    name?: string
    iban?: string
    bank?: string
  } | null
  last_receipt_id?: string
  transaction_id?: string
  [key: string]: unknown
}

export interface ChatResponse {
  session_id: string
  reply: string
  state: PaymentSessionState
}

export interface PaymentIntent {
  recipient_name: string
  recipient_iban?: string
  amount: number
  currency: string
}

export interface PayeeVerificationResult {
  verification_id?: string
  status: VerificationStatus
  mode: 'mock' | 'live'
  session_id: string
  verified_name?: string
  verified_iban?: string
  issuer_bank?: string
  reason?: string
  verified_at?: string
  checks?: Record<string, boolean>
}

export interface PaymentReceipt {
  credential_id: string
  transaction_id: string
  session_id: string
  amount: number
  currency: string
  creditor_iban: string
  creditor_name: string
  status: string
  timestamp: string
  issuer_did: string
  issuer_signature: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
}
