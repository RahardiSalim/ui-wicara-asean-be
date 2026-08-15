from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import Settings


class SupabaseTokenError(ValueError):
    pass


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def verify_supabase_access_token(token: str, settings: Settings) -> dict[str, Any]:
    clean_token = token.strip()
    if not clean_token:
        raise SupabaseTokenError("Invalid Supabase access token.")

    try:
        header = jwt.get_unverified_header(clean_token)
    except jwt.PyJWTError as exc:
        raise SupabaseTokenError("Invalid Supabase access token.") from exc

    algorithm = str(header.get("alg") or "").upper()
    if algorithm.startswith("HS"):
        return _verify_hs_access_token(clean_token, settings, algorithm)

    try:
        signing_key = _jwks_client(settings.supabase_jwks_url).get_signing_key_from_jwt(clean_token)
        return jwt.decode(
            clean_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.supabase_jwt_audience,
            issuer=settings.supabase_issuer,
            options={"require": ["sub", "iss", "aud", "exp"]},
        )
    except jwt.PyJWTError as exc:
        try:
            return _verify_with_supabase_auth_server(clean_token, settings)
        except SupabaseTokenError:
            raise SupabaseTokenError("Invalid Supabase access token.") from exc


def _verify_hs_access_token(token: str, settings: Settings, algorithm: str) -> dict[str, Any]:
    if settings.supabase_jwt_secret:
        try:
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=[algorithm],
                audience=settings.supabase_jwt_audience,
                issuer=settings.supabase_issuer,
                options={"require": ["sub", "iss", "aud", "exp"]},
            )
        except jwt.PyJWTError as exc:
            raise SupabaseTokenError("Invalid Supabase access token.") from exc
    return _verify_with_supabase_auth_server(token, settings)


def _verify_with_supabase_auth_server(token: str, settings: Settings) -> dict[str, Any]:
    """
    Fallback verification path for projects that still use HS* JWT signing.
    Supabase validates the token for us and returns canonical user profile data.
    """
    if not settings.supabase_anon_key:
        raise SupabaseTokenError("SUPABASE_ANON_KEY is missing on backend.")

    url = f"{settings.supabase_project_url.rstrip('/')}/auth/v1/user"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {token}",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=headers)
        if response.status_code >= 400:
            raise SupabaseTokenError(_supabase_error_message(response))
        data = response.json()
    except httpx.HTTPError as exc:
        raise SupabaseTokenError(f"Supabase auth request failed: {exc}") from exc

    sub = str(data.get("id") or "").strip()
    if not sub:
        raise SupabaseTokenError("Supabase user payload missing id.")

    # Preserve expiration/issued-at claims (if present) from token payload for consistency.
    try:
        raw_claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            },
            algorithms=["HS256", "HS384", "HS512", "RS256", "ES256"],
        )
    except jwt.PyJWTError:
        raw_claims = {}

    claims: dict[str, Any] = dict(raw_claims if isinstance(raw_claims, dict) else {})
    claims["sub"] = sub
    claims["aud"] = data.get("aud") or claims.get("aud") or settings.supabase_jwt_audience
    claims["iss"] = claims.get("iss") or settings.supabase_issuer
    claims["email"] = data.get("email")
    claims["phone"] = data.get("phone")
    claims["app_metadata"] = data.get("app_metadata") or claims.get("app_metadata") or {}
    claims["user_metadata"] = data.get("user_metadata") or claims.get("user_metadata") or {}

    if "exp" not in claims:
        raise SupabaseTokenError("Supabase access token has no exp claim.")

    return claims


async def sign_in_with_password(
    *,
    settings: Settings,
    email_or_phone: str,
    password: str,
) -> tuple[str, str]:
    if not settings.supabase_anon_key:
        raise SupabaseTokenError("SUPABASE_ANON_KEY is missing on backend.")
    payload = {"password": password}
    if "@" in email_or_phone:
        payload["email"] = email_or_phone.strip()
    else:
        payload["phone"] = email_or_phone.strip()
    return await _token_exchange(
        settings=settings,
        grant_type="password",
        payload=payload,
    )


async def register_with_password(
    *,
    settings: Settings,
    email: str,
    password: str,
    display_name: str,
    role: str,
) -> tuple[str, str]:
    service_role_key = settings.supabase_service_role_key.strip()
    if not service_role_key:
        raise SupabaseTokenError(
            "SUPABASE_SERVICE_ROLE_KEY is missing on backend; password registration is unavailable."
        )
    signup_url = f"{settings.supabase_project_url.rstrip('/')}/auth/v1/admin/users"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "email": email.strip(),
        "password": password,
        "email_confirm": True,
        "user_metadata": {"display_name": display_name.strip()},
        "app_metadata": {"role": role.strip().lower()},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(signup_url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseTokenError(_supabase_error_message(response))
    except httpx.HTTPError as exc:
        raise SupabaseTokenError(f"Supabase auth request failed: {exc}") from exc
    return await sign_in_with_password(
        settings=settings,
        email_or_phone=email,
        password=password,
    )


async def request_password_reset(
    *,
    settings: Settings,
    email: str,
) -> None:
    if not settings.supabase_anon_key:
        raise SupabaseTokenError("SUPABASE_ANON_KEY is missing on backend.")
    recover_url = f"{settings.supabase_project_url.rstrip('/')}/auth/v1/recover"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                recover_url,
                headers=headers,
                json={"email": email.strip()},
            )
        if response.status_code >= 400:
            raise SupabaseTokenError(_supabase_error_message(response))
    except httpx.HTTPError as exc:
        raise SupabaseTokenError(f"Supabase auth request failed: {exc}") from exc


async def sign_in_with_google_id_token(
    *,
    settings: Settings,
    id_token: str,
    access_token: str | None = None,
    nonce: str | None = None,
) -> tuple[str, str]:
    if not settings.supabase_anon_key:
        raise SupabaseTokenError("SUPABASE_ANON_KEY is missing on backend.")
    payload: dict[str, str] = {
        "provider": "google",
        "id_token": id_token,
    }
    if access_token:
        payload["access_token"] = access_token
    if nonce:
        payload["nonce"] = nonce
    return await _token_exchange(
        settings=settings,
        grant_type="id_token",
        payload=payload,
    )


async def refresh_access_token(
    *,
    settings: Settings,
    refresh_token: str,
) -> tuple[str, str]:
    """Exchange a Supabase refresh_token for a new (access_token, refresh_token) pair."""
    return await _token_exchange(
        settings=settings,
        grant_type="refresh_token",
        payload={"refresh_token": refresh_token},
    )


async def _token_exchange(
    *,
    settings: Settings,
    grant_type: str,
    payload: dict[str, str],
) -> tuple[str, str]:
    """Returns (access_token, refresh_token)."""
    token_url = f"{settings.supabase_project_url.rstrip('/')}/auth/v1/token"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Content-Type": "application/json",
    }
    params = {"grant_type": grant_type}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(token_url, params=params, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseTokenError(_supabase_error_message(response))
        data = response.json()
        access_token = str(data.get("access_token") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
        if not access_token:
            raise SupabaseTokenError("Supabase auth response has no access_token.")
        return access_token, refresh_token
    except httpx.HTTPError as exc:
        raise SupabaseTokenError(f"Supabase auth request failed: {exc}") from exc


def _supabase_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Supabase auth failed with status {response.status_code}."
    for key in ("msg", "message", "error_description", "error"):
        value = data.get(key)
        if value:
            return str(value)
    return f"Supabase auth failed with status {response.status_code}."
