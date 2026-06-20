from __future__ import annotations

from typing import Any

import httpx

from app.modules.speech.errors import SpeechProviderError
from app.modules.speech.schemas import SttRequest, SttResponse, TtsRequest


_OPENROUTER_SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
_OPENROUTER_TRANSCRIPTIONS_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
_TTS_MODEL = "google/gemini-3.1-flash-tts-preview"
_STT_MODEL = "openai/whisper-large-v3-turbo"
_TTS_TIMEOUT_SECONDS = 60.0
_STT_TIMEOUT_SECONDS = 30.0


async def synthesize_speech(request: TtsRequest, api_key: str) -> bytes:
    headers = _headers(api_key)
    payload = {
        "model": _TTS_MODEL,
        "input": request.text,
        "voice": request.voice,
        "response_format": "pcm",
    }
    try:
        async with httpx.AsyncClient(timeout=_TTS_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _OPENROUTER_SPEECH_URL,
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise SpeechProviderError(f"OpenRouter TTS request failed: {exc}") from exc

    if response.status_code != 200:
        raise SpeechProviderError(
            "OpenRouter TTS request failed with status "
            f"{response.status_code}: {_provider_error_message(response)}"
        )
    if not response.content:
        raise SpeechProviderError("OpenRouter TTS returned an empty audio response.")
    return response.content


async def transcribe_audio(request: SttRequest, api_key: str) -> SttResponse:
    headers = _headers(api_key)
    payload = {
        "model": _STT_MODEL,
        "input_audio": {
            "data": request.audio_b64,
            "format": "wav",
        },
        "language": request.locale[:2].lower(),
    }
    try:
        async with httpx.AsyncClient(timeout=_STT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _OPENROUTER_TRANSCRIPTIONS_URL,
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise SpeechProviderError(f"OpenRouter STT request failed: {exc}") from exc

    if response.status_code != 200:
        raise SpeechProviderError(
            "OpenRouter STT request failed with status "
            f"{response.status_code}: {_provider_error_message(response)}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise SpeechProviderError("OpenRouter STT response was not valid JSON.") from exc

    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise SpeechProviderError("OpenRouter STT returned no transcript text.")

    usage = data.get("usage") if isinstance(data, dict) else None
    duration = usage.get("seconds", 0.0) if isinstance(usage, dict) else 0.0
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError) as exc:
        raise SpeechProviderError(
            "OpenRouter STT returned an invalid usage duration."
        ) from exc

    return SttResponse(
        text=text.strip(),
        duration_seconds=max(0.0, duration_seconds),
    )


def _headers(api_key: str) -> dict[str, str]:
    normalized_key = api_key.strip()
    if not normalized_key:
        raise SpeechProviderError("OPENROUTER_API_KEY is missing.")
    return {
        "Authorization": f"Bearer {normalized_key}",
        "Content-Type": "application/json",
    }


def _provider_error_message(response: httpx.Response) -> str:
    try:
        data: Any = response.json()
    except ValueError:
        return response.text.strip() or "No error body returned."

    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if data.get("message"):
            return str(data["message"])
    return str(data)
