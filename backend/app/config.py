"""Application configuration using Pydantic Settings."""

from functools import lru_cache
import os
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the conversational payments backend."""

    # Google Gemini & ADK
    GEMINI_API_KEY: str = ""
    MODEL_NAME: str = "gemini-2.5-flash"
    APP_NAME: str = "conversational_payments"

    # Hexagonal Architecture Adapter Mode
    ADAPTER_MODE: Literal["MOCK", "LIVE"] = "MOCK"

    # Server & Cloud Run
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    LOG_LEVEL: str = "INFO"
    PROJECT_ID: str = "conversational-payments-demo"

    # Live external endpoints (used when ADAPTER_MODE=LIVE)
    LIVE_VERIFIER_URL: str = "https://verifier.example.com"
    LIVE_CORE_BANKING_URL: str = "https://banking.example.com"
    VERIFIER_TIMEOUT_SECONDS: float = 10.0
    RECOVERY_ISSUER_URL: str = "https://issuer.example.com/attestations/iban"

    # Authorization policy: execute immediately only after an external VERIFIED verdict.
    AUTO_EXECUTE_VERIFIED_PAYMENTS: bool = True
    ALLOW_MOCK_VERIFICATION: bool = True
    MOCK_WALLET_AUTO_PRESENT: bool = True
    TRUSTED_FAVORITES: dict[str, str] = {}
    FAVORITE_FAST_PATH_MAX_AMOUNT: float = 500.0
    VERIFICATION_MAX_AGE_SECONDS: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns cached application settings instance."""
    settings = Settings()
    if settings.GEMINI_API_KEY and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = settings.GEMINI_API_KEY
    return settings
