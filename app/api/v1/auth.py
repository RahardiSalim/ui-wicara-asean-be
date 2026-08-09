from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.modules.accounts.dependencies import (
    bearer_token,
    verify_supabase_token_or_401,
)
from app.modules.accounts.schemas import (
    AuthSessionResponse,
    GoogleSignInRequest,
    PasswordRegisterRequest,
    PasswordSignInRequest,
    SupabaseAuthRequest,
    UserAccountRead,
)
from app.modules.accounts.models import UserAccount
from app.modules.accounts.service import get_learner_profile, resolve_role, sync_supabase_user
from app.modules.accounts.supabase import (
    SupabaseTokenError,
    refresh_access_token,
    register_with_password,
    request_password_reset,
    sign_in_with_google_id_token,
    sign_in_with_password,
)

router = APIRouter(prefix="/auth")
logger = logging.getLogger(__name__)

_ALLOWED_ROLES = {"learner", "teacher"}


def _requested_role(role: str) -> str:
    cleaned = (role or "").strip().lower()
    if cleaned not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role must be either learner or teacher.",
        )
    return cleaned


def _authoritative_role(
    claims: dict[str, object],
    *,
    settings: Settings,
    requested_role: str | None = None,
) -> str:
    # User metadata is client-controlled. Never let it grant the teacher role.
    safe_claims = dict(claims)
    user_metadata = safe_claims.get("user_metadata")
    if isinstance(user_metadata, dict):
        safe_metadata = dict(user_metadata)
        safe_metadata.pop("role", None)
        safe_claims["user_metadata"] = safe_metadata

    role = resolve_role(safe_claims, settings)
    if requested_role is not None:
        requested = _requested_role(requested_role)
        if requested == "teacher" and role != "teacher":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account is not authorized for the teacher role.",
            )
    return role


def _auth_session_response(
    session: Session,
    *,
    account: UserAccount,
    token: str,
    refresh_token: str = "",
) -> AuthSessionResponse:
    profile = get_learner_profile(session, account)
    return AuthSessionResponse(
        user_id=str(account.id),
        display_name=account.display_name,
        role=account.role,
        token=token,
        refresh_token=refresh_token,
        email=account.email,
        onboarding_completed=bool(profile and profile.onboarding_completed),
    )


@router.post("/supabase", response_model=AuthSessionResponse)
def authenticate_with_supabase(
    payload: SupabaseAuthRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    claims = verify_supabase_token_or_401(payload.access_token, settings)
    role = _authoritative_role(claims, settings=settings, requested_role=payload.role)
    account = sync_supabase_user(session, claims=claims, role=role)
    return _auth_session_response(session, account=account, token=payload.access_token)


@router.post("/sign-in", response_model=AuthSessionResponse)
async def sign_in_with_backend(
    payload: PasswordSignInRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    _requested_role(payload.role)
    try:
        access_token, supabase_refresh_token = await sign_in_with_password(
            settings=settings,
            email_or_phone=payload.email_or_phone,
            password=payload.password,
        )
    except SupabaseTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    claims = verify_supabase_token_or_401(access_token, settings)
    role = _authoritative_role(claims, settings=settings, requested_role=payload.role)
    account = sync_supabase_user(session, claims=claims, role=role)
    return _auth_session_response(
        session, account=account, token=access_token, refresh_token=supabase_refresh_token
    )


@router.post("/register", response_model=AuthSessionResponse)
async def register_with_backend(
    payload: PasswordRegisterRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    requested_role = _requested_role(payload.role)
    try:
        access_token, supabase_refresh_token = await register_with_password(
            settings=settings,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            role=requested_role,
        )
    except SupabaseTokenError as exc:
        logger.warning("Supabase registration failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to register this account.",
        ) from exc
    claims = verify_supabase_token_or_401(access_token, settings)
    role = _authoritative_role(claims, settings=settings, requested_role=requested_role)
    account = sync_supabase_user(session, claims=claims, role=role)
    return _auth_session_response(
        session, account=account, token=access_token, refresh_token=supabase_refresh_token
    )


@router.post("/google", response_model=AuthSessionResponse)
async def sign_in_with_google(
    payload: GoogleSignInRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    _requested_role(payload.role)
    try:
        access_token, supabase_refresh_token = await sign_in_with_google_id_token(
            settings=settings,
            id_token=payload.id_token,
            access_token=payload.access_token,
            nonce=payload.nonce,
        )
    except SupabaseTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    claims = verify_supabase_token_or_401(access_token, settings)
    role = _authoritative_role(claims, settings=settings, requested_role=payload.role)
    account = sync_supabase_user(session, claims=claims, role=role)
    return _auth_session_response(
        session, account=account, token=access_token, refresh_token=supabase_refresh_token
    )


class _RefreshRequest(BaseModel):
    refresh_token: str


class _PasswordResetRequest(BaseModel):
    email: str = Field(..., min_length=3)


class _PasswordResetResponse(BaseModel):
    message: str


@router.post("/password-reset", response_model=_PasswordResetResponse)
async def password_reset(
    payload: _PasswordResetRequest,
    settings: Settings = Depends(get_settings),
) -> _PasswordResetResponse:
    message = "If an account exists for that email, password reset instructions have been sent."
    try:
        await request_password_reset(settings=settings, email=payload.email)
    except SupabaseTokenError as exc:
        # Keep the response generic so this endpoint cannot enumerate accounts.
        logger.warning("Supabase password reset request failed: %s", exc)
    return _PasswordResetResponse(message=message)


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh_session(
    payload: _RefreshRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    """Exchange a Supabase refresh_token for a new access_token + refresh_token pair."""
    try:
        access_token, new_refresh_token = await refresh_access_token(
            settings=settings,
            refresh_token=payload.refresh_token,
        )
    except SupabaseTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    claims = verify_supabase_token_or_401(access_token, settings)
    role = _authoritative_role(claims, settings=settings)
    account = sync_supabase_user(session, claims=claims, role=role)
    return _auth_session_response(
        session, account=account, token=access_token, refresh_token=new_refresh_token
    )


@router.get("/me", response_model=UserAccountRead)
def current_user(
    token: str = Depends(bearer_token),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserAccountRead:
    claims = verify_supabase_token_or_401(token, settings)
    role = _authoritative_role(claims, settings=settings)
    account = sync_supabase_user(session, claims=claims, role=role)
    return UserAccountRead.model_validate(account)
