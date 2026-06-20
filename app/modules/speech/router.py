from __future__ import annotations

import base64
import re

from fastapi import APIRouter, HTTPException, status

from app.modules.ai.config import get_ai_settings
from app.modules.speech.errors import SpeechProviderError
from app.modules.speech.openrouter_speech import synthesize_speech, transcribe_audio
from app.modules.speech.schemas import (
    SttRequest,
    SttResponse,
    TtsChunkResponse,
    TtsRequest,
)


router = APIRouter(tags=["speech"])

_MAX_TTS_CHARS = 800
_SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.DOTALL)


@router.post("/tts", response_model=list[TtsChunkResponse])
async def create_speech(request: TtsRequest) -> list[TtsChunkResponse]:
    chunks = _chunk_text(request.text, max_chars=_MAX_TTS_CHARS)
    total_chunks = len(chunks)
    api_key = get_ai_settings().openrouter_api_key
    responses: list[TtsChunkResponse] = []

    try:
        for chunk_index, chunk in enumerate(chunks):
            audio = await synthesize_speech(
                request.model_copy(update={"text": chunk}),
                api_key,
            )
            responses.append(
                TtsChunkResponse(
                    audio_b64=base64.b64encode(audio).decode("ascii"),
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                )
            )
    except SpeechProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Speech service unavailable",
        ) from exc

    return responses


@router.post("/stt", response_model=SttResponse)
async def create_transcription(request: SttRequest) -> SttResponse:
    try:
        return await transcribe_audio(
            request,
            get_ai_settings().openrouter_api_key,
        )
    except SpeechProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Speech service unavailable",
        ) from exc


def _chunk_text(text: str, *, max_chars: int) -> list[str]:
    normalized = " ".join(text.split())
    sentences = [
        match.group(0).strip()
        for match in _SENTENCE_PATTERN.finditer(normalized)
        if match.group(0).strip()
    ]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence_parts = (
            [sentence]
            if len(sentence) <= max_chars
            else _split_long_sentence(sentence, max_chars=max_chars)
        )
        for part in sentence_parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = part
            else:
                current = candidate

    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, *, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = sentence.strip()
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        split_at = max(window.rfind(","), window.rfind("—"))
        include_punctuation = split_at > 0
        if split_at <= 0:
            split_at = window.rfind(" ")
        if split_at <= 0:
            next_space = remaining.find(" ", max_chars)
            if next_space < 0:
                parts.append(remaining)
                return parts
            split_at = next_space

        end = split_at + 1 if include_punctuation else split_at
        part = remaining[:end].strip()
        if part:
            parts.append(part)
        remaining = remaining[end:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts
