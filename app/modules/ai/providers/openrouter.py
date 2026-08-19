from __future__ import annotations

from typing import Any

import httpx

from app.modules.ai.config import AISettings
from app.modules.ai.errors import AIConfigurationError, AIProviderError
from app.modules.ai.providers.base import AIProvider
from app.modules.ai.schemas import (
    AIGenerationRequest,
    AIGenerationResponse,
    AIImageInput,
    AITextInput,
    AIUsage,
)


class OpenRouterProvider(AIProvider):
    name = "openrouter"

    def __init__(
        self,
        settings: AISettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def generate(self, request: AIGenerationRequest) -> AIGenerationResponse:
        api_key = self._settings.openrouter_api_key.strip()
        if not api_key:
            raise AIConfigurationError("OPENROUTER_API_KEY is missing.")

        payload = self._build_payload(request)
        endpoint = f"{self._settings.openrouter_base_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.ai_request_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise AIProviderError(f"OpenRouter request failed: {exc}") from exc

        if response.status_code >= 400:
            raise AIProviderError(
                f"OpenRouter request failed with status {response.status_code}: "
                f"{_provider_error_message(response)}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AIProviderError("OpenRouter response was not valid JSON.") from exc

        return self._response_from_payload(request, data)

    def _build_payload(self, request: AIGenerationRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})

        content_parts: list[dict[str, Any]] = []
        if request.user_instruction:
            content_parts.append({"type": "text", "text": request.user_instruction})

        for item in request.inputs:
            if isinstance(item, AITextInput):
                content_parts.append({"type": "text", "text": item.text})
            elif isinstance(item, AIImageInput):
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{item.mime_type};base64,{item.as_base64()}",
                        },
                    }
                )

        messages.append({"role": "user", "content": content_parts})

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
        }
        params = request.params.model_dump(exclude_none=True)
        reasoning_effort = self._settings.ai_reasoning_effort.strip()
        if reasoning_effort and "reasoning" not in params:
            payload["reasoning"] = {"effort": reasoning_effort}
        if params:
            payload.update(params)
        provider_routing = self._provider_routing(params.get("provider"))
        if provider_routing:
            payload["provider"] = provider_routing
        return payload

    def _provider_routing(self, requested: Any) -> dict[str, Any]:
        """Merge the caller's provider block with the default routing policy.

        Callers set things like require_parameters per request; the sort order
        is a deployment-wide choice, so it belongs here rather than in every
        call site. An explicit sort from the caller still wins.
        """

        routing: dict[str, Any] = dict(requested) if isinstance(requested, dict) else {}
        sort = self._settings.ai_provider_sort.strip()
        if sort and "sort" not in routing:
            routing["sort"] = sort
        return routing

    def _response_from_payload(
        self,
        request: AIGenerationRequest,
        data: dict[str, Any],
    ) -> AIGenerationResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIProviderError("OpenRouter returned no choices.")

        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        choice_error = first_choice.get("error")
        if isinstance(choice_error, dict):
            message = choice_error.get("message") or choice_error
            raise AIProviderError(f"OpenRouter choice failed: {message}")

        message = first_choice.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        text = _extract_message_text(content)
        if not text:
            finish_reason = first_choice.get("finish_reason")
            raise AIProviderError(
                f"OpenRouter returned no text."
                f"{f' Finish reason: {finish_reason}.' if finish_reason else ''}"
            )

        return AIGenerationResponse(
            provider=self.name,
            model=str(data.get("model") or request.model),
            text=text,
            finish_reason=first_choice.get("finish_reason"),
            usage=_usage_from_payload(data.get("usage")),
            response_id=data.get("id"),
            raw=data,
        )


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(part["text"])
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "\n".join(parts).strip()
    return ""


def _usage_from_payload(value: Any) -> AIUsage | None:
    if not isinstance(value, dict):
        return None
    return AIUsage(
        input_tokens=value.get("prompt_tokens"),
        output_tokens=value.get("completion_tokens"),
        total_tokens=value.get("total_tokens"),
        raw=value,
    )


def _provider_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or "No error body returned."

    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    message = data.get("message")
    if message:
        return str(message)
    return str(data)
