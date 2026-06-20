from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.modules.accounts.models import UserAccount
from app.modules.accounts.service import resolve_role, sync_supabase_user
from app.modules.accounts.supabase import (
    SupabaseTokenError,
    verify_supabase_access_token,
)


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def optional_bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def verified_supabase_claims(
    token: str = Depends(bearer_token),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return verify_supabase_token_or_401(token, settings)


def verify_supabase_token_or_401(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return verify_supabase_access_token(token, settings)
    except SupabaseTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_account(
    claims: dict[str, Any] = Depends(verified_supabase_claims),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserAccount:
    return sync_supabase_user(session, claims=claims, role=resolve_role(claims, settings))


def get_optional_current_account(
    token: str | None = Depends(optional_bearer_token),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserAccount | None:
    if token is None:
        return None

    claims = verify_supabase_token_or_401(token, settings)
    return sync_supabase_user(session, claims=claims, role=resolve_role(claims, settings))


def get_current_teacher(
    account: UserAccount = Depends(get_current_account),
) -> UserAccount:
    if account.role != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher role required.",
        )
    return account
