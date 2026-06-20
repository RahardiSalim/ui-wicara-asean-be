from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1)
    locale: str = Field(..., min_length=2)
    voice: str = Field(default="Aoede", min_length=1)

    @field_validator("text", "locale", "voice")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be empty.")
        return normalized


class TtsChunkResponse(BaseModel):
    audio_b64: str
    chunk_index: int
    total_chunks: int


class SttRequest(BaseModel):
    audio_b64: str = Field(..., min_length=1)
    locale: str = Field(..., min_length=2)

    @field_validator("audio_b64", "locale")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be empty.")
        return normalized


class SttResponse(BaseModel):
    text: str
    duration_seconds: float
