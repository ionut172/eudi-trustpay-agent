"""FastAPI application for ING EUDI Wallet Conversational Payments."""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, List
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from app.adapters.live_adapters import get_adapters
from app.agents.orchestrator import get_orchestrator
from app.config import get_settings
from app.domain.models import (
    ChatRequest,
    ChatResponse,
    PayeeVerificationResult,
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
        "Starting ING EUDI Conversational Payments Service",
        adapter_mode=settings.ADAPTER_MODE,
        port=settings.PORT,
        model=settings.MODEL_NAME,
    )
    # Initialize adapters and orchestrator singleton
    get_adapters(settings)
    get_orchestrator(settings)
    yield
    logger.info("Shutting down ING EUDI Conversational Payments Service")


app = FastAPI(
    title="ING EUDI Wallet Conversational Payments API",
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
