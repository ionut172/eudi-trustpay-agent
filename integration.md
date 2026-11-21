Integrate the real European Digital Identity (EUDI) Wallet OID4VP flow into this FastAPI/Google ADK backend.

Context
- The application currently uses mock OID4VP presentations and synthetic presets.
- The payment flow is: chat payment intent → create verification session → wallet presents credentials → verification verdict → optionally execute SEPA Instant.
- Execution is allowed only if the verifier returns status exactly VERIFIED.
- Mock verification must never be accepted when ADAPTER_MODE=LIVE.

Required implementation

1. Add an abstraction for production wallet verification
- Keep the existing IEUDIVerifierPort interface.
- Implement LiveEUDIVerifierAdapter using the real verifier API or SDK.
- Store live verifier configuration in Settings:
  - LIVE_VERIFIER_URL
  - VERIFIER_TIMEOUT_SECONDS
  - wallet/verifier issuer or trust configuration
  - allowed credentials and trust frameworks
- Do not hardcode credentials or issuer trust rules.

2. Initiate OID4VP correctly
- When initiate_payee_eudi_verification runs:
  - generate a unique verification_id/session_id;
  - generate a cryptographically secure nonce;
  - set the intended audience/verifier identity;
  - store an expiry time;
  - request the required PID and bank account attestation credentials;
  - include the requested IBAN and expected account holder in the DCQL request;
  - create an OID4VP request/deep link or QR URI;
  - persist PENDING status;
  - never mark verification as VERIFIED during initiation.

3. Fetch wallet presentation data
- Implement the real verifier presentation callback or polling endpoint.
- Validate the presentation submission against:
  - nonce;
  - audience/client_id;
  - presentation expiry;
  - credential signatures;
  - issuer certificates or configured trust anchors;
  - credential expiry;
  - credential status/revocation;
  - replay protection;
  - verifier/request binding;
  - holder binding;
  - agreed holder/account-holder relationship.

4. Map external credentials
- Do not assume that PID.sub and BankAccountAttestation.sub are necessarily equal.
- Map the real credential claims after confirming the profile or trust-framework rules.
- Preserve all important verified values:
  - verification_id;
  - status;
  - mode="live";
  - verified_name;
  - verified_iban;
  - issuer_bank;
  - actual_pid_name;
  - actual_iban_owner;
  - reason;
  - verified_at;
  - all verification checks.

Normalize only presentation values needed for comparison:
- IBAN: remove whitespace and uppercase.
- Names: trim whitespace and case-normalize only for display/comparison.
- Never use unverified chat text as the source of verified identity.

5. Compare presentation with payment intent
- Retrieve the original payment intent from the session:
  - creditor_name;
  - creditor_iban;
  - amount;
  - currency.
- Compare the presented and attested IBAN with creditor_iban.
- Compare the account holder and agreed holder binding with creditor_name.
- The intended IBAN must match the verified wallet attestation exactly after normalization.

Return:
- VERIFIED only when every required cryptographic, trust, expiry, holder-binding, name and IBAN check passes.
- REJECTED for ACCOUNT_HOLDER_MISMATCH or IBAN_MISMATCH.
- RECOVERY_REQUIRED when a credential is missing or expired.
- TRUST_ERROR for invalid signatures, untrusted issuers or invalid status.
- PENDING while presentation is incomplete.

6. Secure execution
- Payment execution must not trust conversational claims.
- execute_verified_instant_payment must retrieve the latest verifier result again.
- Refuse execution unless:
  - status == VERIFIED;
  - mode is live in production;
  - verification_id matches the pending session;
  - verification is not expired;
  - payment intent is unchanged;
  - presentation nonce/audience checks passed.
- Reject mock verification if LIVE mode or ALLOW_MOCK_VERIFICATION=false.
- Keep the code-level guardrail even if the LLM or chat UI requests execution.

7. Automatic payment behavior
- After the status check returns VERIFIED and AUTO_EXECUTE_VERIFIED_PAYMENTS=true:
  - execute the instant payment automatically;
  - do not ask the customer for an additional manual confirmation;
  - return the authorization report and SEPA transaction result.
- Do not auto-execute PENDING, REJECTED, mismatch, recovery or trust-error responses.

8. Preserve production safety
- Never include private keys or wallet credentials in logs.
- Log verification_id, status, issuer, session and failure category.
- Use one-time presentation and replay protection.
- Add issuer trust configuration rather than accepting any signed credential.
- Time out unfinished verification requests.
- Use HTTPS callbacks and authenticated service-to-service communication.
- Treat the mock implementation strictly as test code.

9. Update API models and endpoints
- Add explicit live/mock mode and verification checks to PayeeVerificationResult.
- Add or align endpoints for:
  - creating verification;
  - presentation callback;
  - polling verification status;
  - recovery;
  - instant payment execution.
- The public response must preserve a stable status contract:
  - VERIFIED;
  - REJECTED;
  - PENDING;
  - RECOVERY_REQUIRED;
  - TRUST_ERROR.

10. Add tests
- Mock unit tests:
  - exact verified IBAN/name;
  - different IBAN/name;
  - mismatch subjects;
  - missing credential;
  - expired credential;
  - invalid signature;
  - untrusted issuer;
  - revoked credential;
  - replayed presentation;
  - invalid nonce;
  - invalid audience;
  - live rejecting mock verification;
  - pending verification not executing.

- Integration tests:
  - chat intent;
  - wallet presentation;
  - external status check;
  - automatic execution on VERIFIED;
  - permanent block on REJECTED;
  - no execution on PENDING or recovery.

11. Update documentation
- Explain the mock-to-live integration steps.
- Mark mock responses clearly as test-only.
- Document trust framework, issuer profile and credential profile requirements.
- Provide example live environment variables and callback endpoints.

Acceptance criteria
- No payment can execute unless the verifier has produced a fresh VERIFIED live result.
- A wallet IBAN different from the payment intent cannot execute.
- A wallet name/account-holder mismatch cannot execute.
- Mock verification cannot authorize live/core-banking execution.
- Replay, nonce and audience mismatches are blocked.
- The existing mock flow remains usable for development and hackathon demonstrations.
- The final implementation has tests for all core verification and payment guardrails.