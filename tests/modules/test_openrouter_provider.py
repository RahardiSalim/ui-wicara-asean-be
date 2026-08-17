import asyncio
import base64
import json

import httpx
import pytest

from app.modules.ai.client import AIClient
from app.modules.ai.config import AISettings, DEFAULT_AI_IMAGE_MODEL, DEFAULT_AI_MODEL
from app.modules.ai.errors import AIConfigurationError, AIProviderError
from app.modules.ai.providers.openrouter import OpenRouterProvider
from app.modules.ai.schemas import AIGenerationRequest


def _settings(**overrides):
    values = {
        "AI_PROVIDER": "openrouter",
        "AI_MODEL": DEFAULT_AI_MODEL,
        "AI_REASONING_EFFORT": "high",
        "OPENROUTER_API_KEY": "test-openrouter-key",
        "OPENROUTER_BASE_URL": "https://openrouter.test/api/v1",
        "AI_REQUEST_TIMEOUT_SECONDS": 1.0,
    }
    aliases = {
        "ai_provider": "AI_PROVIDER",
        "ai_model": "AI_MODEL",
        "ai_reasoning_effort": "AI_REASONING_EFFORT",
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "openrouter_base_url": "OPENROUTER_BASE_URL",
        "ai_request_timeout_seconds": "AI_REQUEST_TIMEOUT_SECONDS",
    }
    for key, value in overrides.items():
        values[aliases.get(key, key)] = value
    return AISettings(**values)


def _request(**overrides):
    values = {
        "provider": "openrouter",
        "model": DEFAULT_AI_MODEL,
        "user_instruction": "Say hello.",
    }
    values.update(overrides)
    return AIGenerationRequest.model_validate(values)


def test_missing_api_key_raises_configuration_error():
    provider = OpenRouterProvider(_settings(openrouter_api_key=""))

    with pytest.raises(AIConfigurationError, match="OPENROUTER_API_KEY"):
        asyncio.run(provider.generate(_request()))


def test_text_request_uses_openrouter_chat_completion_payload():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "gen-test",
                "model": DEFAULT_AI_MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )

    provider = OpenRouterProvider(_settings(), transport=httpx.MockTransport(handler))
    response = asyncio.run(
        provider.generate(
            _request(
                system_instruction="Return a short greeting.",
                params={
                    "temperature": 0.2,
                    "max_tokens": 64,
                    "response_format": {"type": "json_object"},
                },
            )
        )
    )

    assert response.provider == "openrouter"
    assert response.model == DEFAULT_AI_MODEL
    assert response.text == "Hello"
    assert response.finish_reason == "stop"
    assert response.response_id == "gen-test"
    assert response.usage is not None
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 10

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://openrouter.test/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-openrouter-key"
    body = json.loads(request.content)
    assert body["model"] == DEFAULT_AI_MODEL
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning"] == {"effort": "high"}
    assert body["messages"] == [
        {"role": "system", "content": "Return a short greeting."},
        {"role": "user", "content": [{"type": "text", "text": "Say hello."}]},
    ]


def test_image_input_becomes_data_uri_image_url():
    captured_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "gen-image",
                "model": DEFAULT_AI_MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "I can see it."},
                    }
                ],
            },
        )

    encoded = base64.b64encode(b"fake-image").decode("ascii")
    provider = OpenRouterProvider(_settings(), transport=httpx.MockTransport(handler))
    response = asyncio.run(
        provider.generate(
            _request(
                user_instruction="Describe this image.",
                inputs=[{"type": "image", "mime_type": "image/png", "data": encoded}],
            )
        )
    )

    assert response.text == "I can see it."
    user_content = captured_bodies[0]["messages"][-1]["content"]
    assert user_content == [
        {"type": "text", "text": "Describe this image."},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}"},
        },
    ]


def test_client_routes_image_input_to_the_vision_model():
    client = AIClient(settings=_settings(), providers=[])

    request = client._build_request(
        provider=None,
        model=None,
        system_instruction=None,
        user_instruction="Read the learner work.",
        inputs=[
            {
                "type": "image",
                "mime_type": "image/png",
                "data": base64.b64encode(b"fake-image").decode("ascii"),
            }
        ],
        params=None,
    )

    assert request.model == DEFAULT_AI_IMAGE_MODEL


def test_http_error_body_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={"error": {"code": 402, "message": "Insufficient credits"}},
        )

    provider = OpenRouterProvider(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(AIProviderError, match="402.*Insufficient credits"):
        asyncio.run(provider.generate(_request()))
