from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import LearnerProfile, UserAccount
from app.modules.accounts.schemas import LearnerProfileOnboardingRequest


def sync_supabase_user(
    session: Session,
    *,
    claims: dict[str, Any],
    role: str,
) -> UserAccount:
    supabase_user_id = str(claims["sub"])
    email = _string_or_none(claims.get("email"))
    phone = _string_or_none(claims.get("phone"))
    metadata = _metadata(claims)
    display_name = _display_name(metadata, email, phone)

    account = session.scalar(
        select(UserAccount).where(UserAccount.supabase_user_id == supabase_user_id)
    )
    if account is None:
        account = UserAccount(
            supabase_user_id=supabase_user_id,
            provider_subject=supabase_user_id,
        )
        session.add(account)

    account.email = email
    account.phone = phone
    account.display_name = display_name
    account.role = _normalize_role(role)
    account.auth_provider = str(claims.get("app_metadata", {}).get("provider") or "supabase")
    account.status = "active"
    account.metadata_json = {
        "app_metadata": claims.get("app_metadata", {}),
        "user_metadata": metadata,
    }
    account.last_seen_at = datetime.now(UTC)
    session.commit()
    session.refresh(account)
    return account


def get_learner_profile(session: Session, user: UserAccount) -> LearnerProfile | None:
    return session.scalar(select(LearnerProfile).where(LearnerProfile.user_id == user.id))


def save_onboarding_profile(
    session: Session,
    *,
    user: UserAccount,
    payload: LearnerProfileOnboardingRequest,
) -> LearnerProfile:
    profile = get_learner_profile(session, user)
    if profile is None:
        profile = LearnerProfile(user_id=user.id)
        session.add(profile)

    profile.full_name = payload.full_name.strip()
    profile.country_name = payload.country_name.strip()
    profile.education_level = payload.education_level.strip()
    profile.grade_level = payload.grade_level.strip()
    profile.preferred_language = payload.preferred_language.strip() or "en"
    profile.study_goal = payload.study_goal.strip()
    profile.daily_study_time_label = payload.daily_study_time_label.strip()
    profile.selected_subjects = [
        _normalize_subject_code(code) for code in payload.selected_subjects
    ]
    profile.onboarding_completed = True

    if profile.full_name:
        user.display_name = profile.full_name

    session.commit()
    session.refresh(profile)
    return profile


def _normalize_role(role: str) -> str:
    cleaned = (role or "").strip().lower()
    return cleaned if cleaned in {"learner", "teacher"} else "learner"


def resolve_role(claims: dict[str, Any], settings: Any) -> str:
    """Derive the account role from a teacher allowlist or trusted claims.

    User metadata is user-controlled and therefore cannot grant teacher access.
    Falls back to ``learner`` when nothing trusted matches. Kept dependency-free
    of the Settings type so it can be called wherever a settings object is handy.
    """
    email = _string_or_none(claims.get("email"))
    if email and email.lower() in settings.teacher_email_set:
        return "teacher"
    app_metadata = claims.get("app_metadata") or {}
    claimed = app_metadata.get("role") if isinstance(app_metadata, dict) else None
    return _normalize_role(str(claimed))


def _metadata(claims: dict[str, Any]) -> dict[str, Any]:
    value = claims.get("user_metadata") or claims.get("raw_user_meta_data") or {}
    return value if isinstance(value, dict) else {}


def _display_name(
    metadata: dict[str, Any],
    email: str | None,
    phone: str | None,
) -> str:
    for key in ("full_name", "name", "display_name"):
        value = _string_or_none(metadata.get(key))
        if value:
            return value
    return email or phone or "Learner"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_subject_code(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")
