"""FastAPI application for the EUDI conversational payments prototype."""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, List
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from app.adapters.live_adapters import get_adapters
from app.adapters.mock_wallet_api import MockEUWalletApi
from app.agents.orchestrator import get_orchestrator
from app.config import get_settings
from app.domain.models import (
    ChatRequest,
    ChatResponse,
    PayeeVerificationResult,
    PaymentIntentRequest,
    PaymentReceiptVC,
    VerificationStatus,
    WalletPresentationPayload,
)
from app.telemetry import get_logger, setup_telemetry

settings = get_settings()
setup_telemetry(log_level=settings.LOG_LEVEL)
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle handler."""
    logger.info(
        "Starting conversational payments service",
        adapter_mode=settings.ADAPTER_MODE,
        port=settings.PORT,
        model=settings.MODEL_NAME,
    )
    # Initialize adapters and orchestrator singleton
    get_adapters(settings)
    get_orchestrator(settings)
    yield
    logger.info("Shutting down conversational payments service")


app = FastAPI(
    title="EUDI Conversational Payments API",
    description="Conversational banking payments secured by EUDI Wallet verification (OID4VP / DCQL / OID4VCI)",
    version="1.0.0",
    lifespan=lifespan,
)

# Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Cloud Run Health Probes ---


@app.get("/healthz", tags=["Health"])
async def healthz() -> dict[str, Any]:
    """Cloud Run liveness probe."""
    return {"status": "ok", "adapter_mode": settings.ADAPTER_MODE}


@app.get("/readyz", tags=["Health"])
async def readyz() -> dict[str, Any]:
    """Cloud Run readiness probe."""
    return {"status": "ready", "adapter_mode": settings.ADAPTER_MODE}


# --- Conversational Endpoint ---


@app.post("/api/chat", response_model=ChatResponse, tags=["Conversational Payments"])
async def chat(request: ChatRequest) -> ChatResponse:
    """Interacts with the Google ADK Payment Orchestrator Agent."""
    orchestrator = get_orchestrator(settings)
    try:
        reply, state = await orchestrator.process_message(
            session_id=request.session_id,
            user_id=request.user_id,
            message=request.message,
        )
        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            state=state,
        )
    except Exception as e:
        logger.error(
            f"Error processing chat message: {e}",
            session_id=request.session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat processing failed: {str(e)}",
        )


@app.post("/api/payment-intents", response_model=ChatResponse, tags=["Conversational Payments"])
async def submit_payment_intent(request: PaymentIntentRequest) -> ChatResponse:
    """Accepts structured intent when conversational LLM extraction is unavailable."""
    orchestrator = get_orchestrator(settings)
    reply, state = await orchestrator.process_intent(
        session_id=request.session_id,
        user_id=request.user_id,
        intent=request.intent,
    )
    return ChatResponse(session_id=request.session_id, reply=reply, state=state)


@app.post("/api/payments/{session_id}/verification", response_model=ChatResponse, tags=["Conversational Payments"])
async def check_payment_status(session_id: str, user_id: str = "user-1") -> ChatResponse:
    """Checks external verifier status and follows configured execution policy."""
    orchestrator = get_orchestrator(settings)
    reply, state = await orchestrator.check_verification(session_id, user_id)
    return ChatResponse(session_id=session_id, reply=reply, state=state)


@app.post("/api/payments/{session_id}/execute", response_model=ChatResponse, tags=["Conversational Payments"])
async def execute_payment(session_id: str, user_id: str = "user-1") -> ChatResponse:
    """Attempts payment execution; backend authorization guard remains authoritative."""
    orchestrator = get_orchestrator(settings)
    reply, state = await orchestrator.execute_payment(session_id, user_id)
    return ChatResponse(session_id=session_id, reply=reply, state=state)


# --- Mock EUDI Wallet Simulator Endpoints ---


async def _sync_adk_session_verification(
    session_id: str,
    result: PayeeVerificationResult,
) -> None:
    """Syncs the verifier evaluation result into the ADK session state."""
    orchestrator = get_orchestrator(settings)
    session = await orchestrator.get_or_create_session(user_id="user-1", session_id=session_id)
    session.state["verification_status"] = result.status.value
    if result.status == VerificationStatus.VERIFIED:
        session.state["verified_payee"] = {
            "name": result.verified_name,
            "iban": result.verified_iban,
            "bank": result.issuer_bank,
        }
    orchestrator._sync_storage_state(session)


@app.post(
    "/mock-wallet/{session_id}/present",
    response_model=PayeeVerificationResult,
    tags=["Mock EUDI Wallet Simulator"],
)
async def submit_presentation(
    session_id: str,
    payload: WalletPresentationPayload,
) -> PayeeVerificationResult:
    """Accepts raw OID4VP presentation payload from the EUDI Wallet."""
    verifier, _ = get_adapters(settings)
    result = await verifier.submit_wallet_presentation(session_id=session_id, payload=payload)
    await _sync_adk_session_verification(session_id, result)
    return result


@app.post(
    "/mock-wallet/{session_id}/preset/{scenario}",
    response_model=PayeeVerificationResult,
    tags=["Mock EUDI Wallet Simulator"],
)
async def apply_preset(
    session_id: str,
    scenario: str,
) -> PayeeVerificationResult:
    """Rapid test trigger applying preset scenarios: valid, mismatch, or missing_iban."""
    verifier, _ = get_adapters(settings)
    try:
        result = await verifier.apply_preset_scenario(session_id=session_id, scenario=scenario)
        await _sync_adk_session_verification(session_id, result)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@app.post(
    "/mock-wallet/{session_id}/recover-iban",
    response_model=PayeeVerificationResult,
    tags=["Mock EUDI Wallet Simulator"],
)
async def recover_iban(session_id: str) -> PayeeVerificationResult:
    """Simulates the payee issuing their missing IBAN attestation and re-evaluates session to VERIFIED."""
    verifier, _ = get_adapters(settings)
    result = await verifier.recover_iban_attestation(session_id=session_id)
    await _sync_adk_session_verification(session_id, result)
    return result


@app.get(
    "/mock-wallet/{session_id}/receipts",
    response_model=List[PaymentReceiptVC],
    tags=["Mock EUDI Wallet Simulator"],
)
async def get_receipts(session_id: str) -> List[PaymentReceiptVC]:
    """Retrieves all signed PaymentReceiptVC credentials issued back to the wallet."""
    _, core_banking = get_adapters(settings)
    return await core_banking.get_receipts(session_id=session_id)


@app.get("/mock-eu-wallet/wallets", tags=["Mock EU Wallet"])
async def list_mock_wallets() -> dict[str, Any]:
    """Lists the synthetic wallets available in the JSON fixture."""
    wallet_api = MockEUWalletApi()
    return {"wallets": wallet_api.list_wallet_ids()}


@app.post(
    "/mock-eu-wallet/{wallet_id}/present/{session_id}",
    response_model=PayeeVerificationResult,
    tags=["Mock EU Wallet"],
)
async def present_mock_wallet(wallet_id: str, session_id: str) -> PayeeVerificationResult:
    """Fetches credentials from the JSON fixture and submits them to the verifier."""
    wallet_api = MockEUWalletApi()
    verifier, _ = get_adapters(settings)
    request = await verifier.get_dcql_request(session_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active wallet request exists for this session.",
        )
    payload = await wallet_api.fetch_wallet_presentation(
        session_id=session_id,
        wallet_id=wallet_id,
        target_iban=request.target_iban,
    )
    payload.nonce = request.nonce
    result = await verifier.submit_wallet_presentation(session_id=session_id, payload=payload)
    await _sync_adk_session_verification(session_id, result)
    return result
