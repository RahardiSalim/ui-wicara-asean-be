from __future__ import annotations

from typing import Any, Iterable

from pydantic import ValidationError

from app.modules.ai.config import AISettings, get_ai_settings
from app.modules.ai.errors import AIInputError, AIProviderNotFoundError
from app.modules.ai.providers import AIProvider, OpenRouterProvider
from app.modules.ai.schemas import AIGenerationRequest, AIGenerationResponse


class AIClient:
    def __init__(
        self,
        *,
        settings: AISettings | None = None,
        providers: Iterable[AIProvider] | None = None,
    ) -> None:
        self._settings = settings or get_ai_settings()
        self._providers: dict[str, AIProvider] = {}
        for provider in providers or (OpenRouterProvider(self._settings),):
            self.register_provider(provider)

    def register_provider(self, provider: AIProvider) -> None:
        name = provider.name.strip().lower()
        if not name:
            raise ValueError("AI provider name cannot be empty.")
        self._providers[name] = provider

    async def generate(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        system_instruction: str | None = None,
        user_instruction: str | None = None,
        inputs: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> AIGenerationResponse:
        request = self._build_request(
            provider=provider,
            model=model,
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            inputs=inputs,
            params=params,
        )
        selected_provider = self._providers.get(request.provider.lower())
        if selected_provider is None:
            supported = ", ".join(sorted(self._providers)) or "none"
            raise AIProviderNotFoundError(
                f"Unsupported AI provider '{request.provider}'. Supported providers: {supported}."
            )
        return await selected_provider.generate(request)

    def _build_request(
        self,
        *,
        provider: str | None,
        model: str | None,
        system_instruction: str | None,
        user_instruction: str | None,
        inputs: list[dict[str, Any]] | None,
        params: dict[str, Any] | None,
    ) -> AIGenerationRequest:
        try:
            return AIGenerationRequest.model_validate(
                {
                    "provider": provider or self._settings.ai_provider,
                    "model": model
                    or (
                        self._settings.ai_image_model
                        if _contains_image_input(inputs)
                        else self._settings.ai_model
                    ),
                    "system_instruction": system_instruction,
                    "user_instruction": user_instruction,
                    "inputs": inputs or [],
                    "params": params or {},
                }
            )
        except ValidationError as exc:
            raise AIInputError(f"Invalid AI request: {exc}") from exc


ai_client = AIClient()


def _contains_image_input(inputs: list[dict[str, Any]] | None) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "image"
        for item in inputs or []
    )
