"""
Задача 2. Управление конфигурацией через Pydantic Settings.
Все переменные окружения с валидацией, поддержка .env и .env.local.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Telegram ──────────────────────────────────────────────────────────
    bot_token: str = Field(..., description="Telegram Bot API token")

    # ── Groq ──────────────────────────────────────────────────────────────
    groq_api_key: str = Field(..., description="Groq Cloud API key")
    groq_model_fast: str = Field(
        default="llama-3.1-8b-instant",
        description="Fast model for short questions",
    )
    groq_model_smart: str = Field(
        default="llama-3.3-70b-versatile",
        description="Smart model for complex questions",
    )
    groq_model_vision: str = Field(
        default="llama-3.2-11b-vision-preview",
        description="Vision model for image analysis",
    )
    groq_model_whisper: str = Field(
        default="whisper-large-v3",
        description="Whisper model for audio transcription",
    )
    groq_timeout: int = Field(default=10, ge=1, le=120)

    # ── Rate limits ────────────────────────────────────────────────────────
    rate_limit_per_user: int = Field(default=10, ge=1)       # req/min
    rate_limit_per_chat: int = Field(default=50, ge=1)       # req/min
    rate_limit_global_rpm: int = Field(default=30, ge=1)     # Groq RPM
    rate_limit_global_tpm: int = Field(default=6000, ge=100) # Groq TPM

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
        description="Redis connection URL. Populated from REDIS_URL env var on Railway.",
    )

    # ── PostgreSQL ─────────────────────────────────────────────────────────
    database_url: Optional[str] = Field(default=None)

    # ── Ollama ─────────────────────────────────────────────────────────────
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="llama3.2:3b")

    # ── Webhook ────────────────────────────────────────────────────────────
    webhook_host: Optional[str] = Field(default=None)
    webhook_port: int = Field(default=8443)
    webhook_path: str = Field(default="/webhook")

    # ── External APIs ──────────────────────────────────────────────────────
    tavily_api_key: Optional[str] = Field(default=None)
    replicate_api_token: Optional[str] = Field(default=None)
    hf_api_token: Optional[str] = Field(default=None)
    google_client_id: Optional[str] = Field(default=None)
    google_client_secret: Optional[str] = Field(default=None)
    fernet_key: Optional[str] = Field(default=None)

    # ── Logging / Tracing ─────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="bot.log")
    jaeger_endpoint: Optional[str] = Field(default=None)

    # ── MCP ───────────────────────────────────────────────────────────────
    mcp_bearer_token: str = Field(default="changeme-secret-token")
    mcp_http_port: int = Field(default=8001)

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, v: str) -> str:
        if not v or ":" not in v:
            raise ValueError(
                "BOT_TOKEN is invalid. Expected format: '123456:ABCdef...'"
            )
        return v

    @field_validator("groq_api_key")
    @classmethod
    def validate_groq_key(cls, v: str) -> str:
        if not v or not v.startswith("gsk_"):
            raise ValueError(
                "GROQ_API_KEY is invalid. Must start with 'gsk_'."
            )
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return v


def get_settings() -> Settings:
    """Load settings, print human-readable error and exit on failure."""
    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError
        print("❌ Configuration error — бот не может запуститься:\n")
        print(str(exc))
        sys.exit(1)


# Singleton
settings = get_settings()