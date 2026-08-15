from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.accounts import supabase
from app.modules.accounts.service import resolve_role


class _Response:
    status_code = 200


class _AsyncClient:
    def __init__(self, calls: list[dict[str, Any]], **_: Any) -> None:
        self._calls = calls

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self._calls.append({"url": url, **kwargs})
        return _Response()


def test_confirmed_registration_uses_admin_api_then_signs_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    sign_in_call: dict[str, Any] = {}
    settings = SimpleNamespace(
        supabase_project_url="https://example.supabase.co/",
        supabase_service_role_key="service-role-key",
    )

    monkeypatch.setattr(
        supabase.httpx,
        "AsyncClient",
        lambda **kwargs: _AsyncClient(calls, **kwargs),
    )

    async def fake_sign_in_with_password(**kwargs: Any) -> tuple[str, str]:
        sign_in_call.update(kwargs)
        return "access-token", "refresh-token"

    monkeypatch.setattr(supabase, "sign_in_with_password", fake_sign_in_with_password)

    tokens = asyncio.run(
        supabase.register_with_password(
            settings=settings,
            email=" new@example.com ",
            password="secret123",
            display_name=" New User ",
            role="teacher",
        )
    )

    assert tokens == ("access-token", "refresh-token")
    assert calls == [
        {
            "url": "https://example.supabase.co/auth/v1/admin/users",
            "headers": {
                "apikey": "service-role-key",
                "Authorization": "Bearer service-role-key",
                "Content-Type": "application/json",
            },
            "json": {
                "email": "new@example.com",
                "password": "secret123",
                "email_confirm": True,
                "user_metadata": {"display_name": "New User"},
                "app_metadata": {"role": "teacher"},
            },
        }
    ]
    assert sign_in_call == {
        "settings": settings,
        "email_or_phone": " new@example.com ",
        "password": "secret123",
    }


def test_registration_requires_service_role_key() -> None:
    settings = SimpleNamespace(supabase_service_role_key="  ")

    with pytest.raises(supabase.SupabaseTokenError, match="SUPABASE_SERVICE_ROLE_KEY"):
        asyncio.run(
            supabase.register_with_password(
                settings=settings,
                email="new@example.com",
                password="secret123",
                display_name="New User",
                role="learner",
            )
        )


def test_password_reset_posts_recover_request_with_anon_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    settings = SimpleNamespace(
        supabase_project_url="https://example.supabase.co/",
        supabase_anon_key="anon-key",
    )
    monkeypatch.setattr(
        supabase.httpx,
        "AsyncClient",
        lambda **kwargs: _AsyncClient(calls, **kwargs),
    )

    asyncio.run(
        supabase.request_password_reset(
            settings=settings,
            email=" user@example.com ",
        )
    )

    assert calls == [
        {
            "url": "https://example.supabase.co/auth/v1/recover",
            "headers": {
                "apikey": "anon-key",
                "Content-Type": "application/json",
            },
            "json": {"email": "user@example.com"},
        }
    ]


def test_user_metadata_role_cannot_self_elevate_to_teacher() -> None:
    claims = {
        "email": "learner@example.com",
        "user_metadata": {"role": "teacher"},
    }
    settings = SimpleNamespace(teacher_email_set=set())

    assert resolve_role(claims, settings) == "learner"


def test_allowlisted_email_resolves_teacher_role() -> None:
    claims = {
        "email": " Teacher@Example.com ",
        "user_metadata": {"role": "learner"},
    }
    settings = SimpleNamespace(teacher_email_set={"teacher@example.com"})

    assert resolve_role(claims, settings) == "teacher"
