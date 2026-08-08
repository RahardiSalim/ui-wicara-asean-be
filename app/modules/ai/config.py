from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_AI_PROVIDER = "openrouter"
DEFAULT_AI_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_AI_REASONING_EFFORT = "high"
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


class AISettings(BaseSettings):
    ai_provider: str = Field(
        default=DEFAULT_AI_PROVIDER,
        validation_alias=AliasChoices("AI_PROVIDER", "WICARA_AI_PROVIDER"),
    )
    ai_model: str = Field(
        default=DEFAULT_AI_MODEL,
        validation_alias=AliasChoices("AI_MODEL", "WICARA_AI_MODEL"),
    )
    ai_reasoning_effort: str = Field(
        default=DEFAULT_AI_REASONING_EFFORT,
        validation_alias=AliasChoices(
            "AI_REASONING_EFFORT",
            "WICARA_AI_REASONING_EFFORT",
        ),
    )
    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "WICARA_OPENROUTER_API_KEY"),
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("OPENROUTER_BASE_URL", "WICARA_OPENROUTER_BASE_URL"),
    )
    ai_request_timeout_seconds: float = Field(
        default=270.0,
        gt=0,
        validation_alias=AliasChoices(
            "AI_REQUEST_TIMEOUT_SECONDS",
            "WICARA_AI_REQUEST_TIMEOUT_SECONDS",
        ),
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_ai_settings() -> AISettings:
    return AISettings()
