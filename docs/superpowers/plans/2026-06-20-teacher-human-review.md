# Teacher / Human-in-the-Loop Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-blocking teacher review layer that flags low-confidence/risky/sampled/learner-flagged AI outputs (generated questions, learning-goal diagnoses, reasoning evaluations), lets teachers approve/reject/correct them in a Flutter teacher dashboard, and measures how often human correction is needed.

**Architecture:** A central `ai_review_items` queue + `ai_review_actions` audit log (two new tables, no schema changes to existing AI tables). A best-effort `flagger` is called by producing services *after* persistence so it never blocks generation. Corrections write back to the source tables via per-type `correctors`. A new `/v1/review/*` router serves a Flutter teacher mode. Metrics are computed from the two new tables.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, Supabase Postgres; Flutter (ChangeNotifier controllers + repository pattern over `ApiClient`).

**User constraints:** Tests are NOT required to run this round (per user). Review is strictly non-blocking. Reject = hide only. Correction depth = approve / reject / edit.

---

## Reference: existing code touchpoints (verified)

- Auth: `app/modules/accounts/dependencies.py` (`get_current_account` hard-codes `role="learner"` via `sync_supabase_user`). Role normalized in `app/modules/accounts/service.py::_normalize_role` (currently forces `"learner"`).
- Models: `AssessmentQuestion`/`AssessmentOption`/`AssessmentQuestionPack`/`AssessmentAttempt`/`AssessmentSession` in `app/modules/learning/models.py`; `LearningGoalResolution` in `app/modules/learning_goal_resolution/models.py`.
- Migrations registered in `app/db/migrations/env.py`; format per `app/db/migrations/versions/20260518_0016_weekly_report_snapshots.py`. Latest head: `20260519_0016_allow_duplicate_goal_nodes` (verify with `alembic heads`).
- Router registration: `app/api/v1/router.py`. `/me`: `app/api/v1/me.py`.
- Config: `app/core/config.py` (`Settings(BaseSettings)` with `Field(..., validation_alias=AliasChoices("WICARA_X","X"))`).

---

## PHASE 1 — Backend

### Task 1: Config — review thresholds, sampling, teacher allowlist

**Files:** Modify `app/core/config.py` (add fields to `Settings`)

- [ ] **Step 1: Add settings fields** (place near other domain settings inside `class Settings`)

```python
    review_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("WICARA_REVIEW_ENABLED", "REVIEW_ENABLED"),
    )
    review_confidence_threshold: float = Field(
        default=0.55, ge=0.0, le=1.0,
        validation_alias=AliasChoices("WICARA_REVIEW_CONFIDENCE_THRESHOLD", "REVIEW_CONFIDENCE_THRESHOLD"),
    )
    review_sample_pct: int = Field(
        default=10, ge=0, le=100,
        validation_alias=AliasChoices("WICARA_REVIEW_SAMPLE_PCT", "REVIEW_SAMPLE_PCT"),
    )
    teacher_emails: str = Field(
        default="",
        validation_alias=AliasChoices("WICARA_TEACHER_EMAILS", "TEACHER_EMAILS"),
    )

    @property
    def teacher_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.teacher_emails.split(",") if e.strip()}
```

- [ ] **Step 2: Verify import** — `python -c "from app.core.config import get_settings; s=get_settings(); print(s.review_confidence_threshold, s.teacher_email_set)"` → prints `0.55 set()` (or `.env` values).

- [ ] **Step 3:** Add to `.env.example`: `TEACHER_EMAILS=`, `REVIEW_CONFIDENCE_THRESHOLD=0.55`, `REVIEW_SAMPLE_PCT=10`, `REVIEW_ENABLED=true`.

---

### Task 2: Teacher role + dependency

**Files:** Modify `app/modules/accounts/service.py`, `app/modules/accounts/dependencies.py`

- [ ] **Step 1:** In `service.py`, allow `teacher` and derive role from claims/allowlist. Replace `_normalize_role`:

```python
def _normalize_role(role: str) -> str:
    cleaned = (role or "").strip().lower()
    return cleaned if cleaned in {"learner", "teacher"} else "learner"


def resolve_role(claims: dict[str, Any], settings) -> str:
    email = _string_or_none(claims.get("email"))
    if email and email.lower() in settings.teacher_email_set:
        return "teacher"
    app_meta = claims.get("app_metadata") or {}
    user_meta = claims.get("user_metadata") or {}
    claimed = app_meta.get("role") or user_meta.get("role") or "learner"
    return _normalize_role(str(claimed))
```

Import `Settings`/`get_settings` is not needed here; `settings` is passed in. (Add nothing else.)

- [ ] **Step 2:** In `dependencies.py`, compute role instead of hard-coding. Update `get_current_account` and `get_optional_current_account`:

```python
from app.modules.accounts.service import resolve_role, sync_supabase_user

def get_current_account(
    claims: dict[str, Any] = Depends(verified_supabase_claims),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserAccount:
    return sync_supabase_user(session, claims=claims, role=resolve_role(claims, settings))
```

Apply the same `role=resolve_role(claims, settings)` in `get_optional_current_account` (it already has `settings`).

- [ ] **Step 3:** Add the teacher gate at the end of `dependencies.py`:

```python
def get_current_teacher(
    account: UserAccount = Depends(get_current_account),
) -> UserAccount:
    if account.role != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Teacher role required.")
    return account
```

- [ ] **Step 4:** Confirm `/me` already returns role — `UserAccountRead` should include `role`. If not, add `role: str` to `UserAccountRead` in `app/modules/accounts/schemas.py`. Verify: `python -c "from app.modules.accounts.schemas import UserAccountRead; print('role' in UserAccountRead.model_fields)"` → `True`.

---

### Task 3: Review models

**Files:** Create `app/modules/review/__init__.py`, `app/modules/review/models.py`

- [ ] **Step 1:** `app/modules/review/models.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base

json_type = JSON().with_variant(JSONB, "postgresql")

ARTIFACT_TYPES = ("question", "diagnosis", "evaluation")
REVIEW_STATUSES = ("open", "approved", "rejected", "corrected")


class AiReviewItem(Base):
    __tablename__ = "ai_review_items"
    __table_args__ = (
        Index(
            "uq_ai_review_items_open_artifact",
            "artifact_type", "artifact_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_ai_review_items_status_priority", "status", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    trigger_reasons: Mapped[list[str]] = mapped_column(json_type, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subject: Mapped[str | None] = mapped_column(String(64))
    concept_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    learner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    first_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    actions: Mapped[list[AiReviewAction]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="AiReviewAction.created_at"
    )


class AiReviewAction(Base):
    __tablename__ = "ai_review_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_review_items.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_accounts.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_json: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    item: Mapped[AiReviewItem] = relationship(back_populates="actions")
```

> NOTE: add `from sqlalchemy import text` to the imports (used in the partial index).

- [ ] **Step 2:** Register models for Alembic. In `app/db/migrations/env.py` add:
`from app.modules.review import models as review_models  # noqa: F401`

- [ ] **Step 3:** Verify import — `python -c "from app.modules.review import models; print(models.AiReviewItem.__tablename__)"` → `ai_review_items`.

---

### Task 4: Alembic migration for the two tables

**Files:** Create `app/db/migrations/versions/20260620_0017_ai_review.py`

- [ ] **Step 1:** Confirm current head: `alembic heads` (expect `20260519_0016_allow_duplicate_goal_nodes`). Set `down_revision` accordingly.

- [ ] **Step 2:** Migration body:

```python
"""add ai review items and actions

Revision ID: 20260620_0017_ai_review
Revises: 20260519_0016_allow_duplicate_goal_nodes
Create Date: 2026-06-20 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260620_0017_ai_review"
down_revision: str | Sequence[str] | None = "20260519_0016_allow_duplicate_goal_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_review_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("trigger_reasons", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subject", sa.String(length=64), nullable=True),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("learner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("first_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_ai_review_items_open_artifact",
        "ai_review_items",
        ["artifact_type", "artifact_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_ai_review_items_status_priority", "ai_review_items", ["status", "priority"])

    op.create_table(
        "ai_review_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["review_item_id"], ["ai_review_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_review_actions_item", "ai_review_actions", ["review_item_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_review_actions_item", table_name="ai_review_actions")
    op.drop_table("ai_review_actions")
    op.drop_index("ix_ai_review_items_status_priority", table_name="ai_review_items")
    op.drop_index("uq_ai_review_items_open_artifact", table_name="ai_review_items")
    op.drop_table("ai_review_items")
```

- [ ] **Step 3:** Apply: `alembic upgrade head`. Verify tables exist (via Supabase MCP `list_tables` once connected, or `\dt` / a `SELECT`).

---

### Task 5: Review schemas

**Files:** Create `app/modules/review/schemas.py`

- [ ] **Step 1:** Pydantic models: `ReviewItemSummary` (queue row), `ReviewItemDetail` (adds `artifact` dict + `actions` list), `ReviewQueueResponse` (items + total), `ApproveRequest`/`RejectRequest`/`CorrectRequest`/`FlagRequest`, `ReviewActionRead`, `ReviewMetricsResponse`. All `from_attributes = True` where reading ORM. `CorrectRequest.fields: dict[str, Any]` (free-form per type, validated in corrector). `FlagRequest`: `artifact_type: str`, `artifact_id: uuid.UUID`, `reason: str`.

---

### Task 6: Flagger

**Files:** Create `app/modules/review/flagger.py`

- [ ] **Step 1:** Public API + helpers (best-effort; never raises):

```python
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.review.models import AiReviewItem

logger = logging.getLogger(__name__)


def _deterministic_sample(artifact_id: uuid.UUID, pct: int) -> bool:
    if pct <= 0:
        return False
    return (hash(str(artifact_id)) % 100) < pct


def evaluate_triggers(*, artifact_type: str, artifact_id: uuid.UUID, confidence: float | None, signals: dict, settings) -> list[str]:
    reasons: list[str] = []
    threshold = settings.review_confidence_threshold
    if confidence is not None and confidence < threshold:
        reasons.append("low_confidence")
    if signals.get("force_low_confidence"):
        reasons.append("low_confidence")
    risky = (
        signals.get("validator_failed")
        or signals.get("generation_source") in {"fallback_generated", "deterministic_fallback"}
        or signals.get("diagnostic_signal") == "misconception_detected"
        or signals.get("structured_parse_ok") is False
        or signals.get("status") == "needs_clarification"
    )
    if risky:
        reasons.append("risk_signal")
    if _deterministic_sample(artifact_id, settings.review_sample_pct):
        reasons.append("sampled")
    # dedupe, preserve order
    seen: set[str] = set()
    return [r for r in reasons if not (r in seen or seen.add(r))]


def flag_artifact(
    session: Session,
    *,
    artifact_type: str,
    artifact_id: uuid.UUID,
    confidence: float | None = None,
    signals: dict | None = None,
    subject: str | None = None,
    concept_id: uuid.UUID | None = None,
    learner_id: uuid.UUID | None = None,
    summary: str = "",
    extra_reason: str | None = None,
) -> None:
    """Best-effort: create/update one OPEN review item if any trigger fires. Never raises."""
    settings = get_settings()
    if not settings.review_enabled:
        return
    try:
        signals = signals or {}
        reasons = evaluate_triggers(
            artifact_type=artifact_type, artifact_id=artifact_id,
            confidence=confidence, signals=signals, settings=settings,
        )
        if extra_reason:
            reasons.append(extra_reason)
        if not reasons:
            return
        item = session.scalar(
            select(AiReviewItem).where(
                AiReviewItem.artifact_type == artifact_type,
                AiReviewItem.artifact_id == artifact_id,
                AiReviewItem.status == "open",
            )
        )
        if item is None:
            item = AiReviewItem(
                artifact_type=artifact_type, artifact_id=artifact_id,
                confidence=confidence, subject=subject, concept_id=concept_id,
                learner_id=learner_id, summary=summary, trigger_reasons=[],
            )
            session.add(item)
        merged = list(dict.fromkeys([*item.trigger_reasons, *reasons]))
        item.trigger_reasons = merged
        item.priority = _priority(merged, confidence)
        if confidence is not None:
            item.confidence = confidence
        session.commit()
    except Exception:  # noqa: BLE001 - review must never break the learner flow
        session.rollback()
        logger.exception("flag_artifact failed for %s %s", artifact_type, artifact_id)


def _priority(reasons: list[str], confidence: float | None) -> int:
    score = 0
    if "learner_flag" in reasons:
        score += 40
    if "risk_signal" in reasons:
        score += 30
    if "low_confidence" in reasons:
        score += 20
    if "sampled" in reasons:
        score += 5
    if confidence is not None:
        score += int((1.0 - confidence) * 10)
    return score
```

> The flagger assigns `trigger_reasons = merged` (reassigns the list so SQLAlchemy detects the JSON change).

---

### Task 7: Correctors (write-back)

**Files:** Create `app/modules/review/correctors.py`

- [ ] **Step 1:** Per-type functions returning `(before_json, after_json)`; raise `ValueError` on bad payload.
  - `correct_question(session, artifact_id, fields)` — load `AssessmentQuestion` (+options); snapshot prompt/expected_reasoning/rubric_json/options; apply `fields` keys among `{"prompt","expected_reasoning","rubric_json","options"}` where `options` is `[{"id"|"option_key","text","is_correct"}]`.
  - `correct_diagnosis(session, artifact_id, fields)` — load `LearningGoalResolution`; apply `suggested_concept_id` (uuid) and set `status="confirmed"`, `confirmed_at=now`.
  - `correct_evaluation(session, artifact_id, fields)` — load `AssessmentAttempt`; apply `reasoning_score`/`diagnostic_signal` and merge `evaluated_result` with `{"teacher_feedback": ...}`.
  - `CORRECTORS = {"question": correct_question, "diagnosis": correct_diagnosis, "evaluation": correct_evaluation}`.

---

### Task 8: Artifact resolver + service

**Files:** Create `app/modules/review/resolver.py`, `app/modules/review/service.py`

- [ ] **Step 1:** `resolver.py` — `resolve_artifact(session, artifact_type, artifact_id) -> dict | None` renders each type to a display dict (question: prompt, options, correct key, expected_reasoning, rubric, llm_metadata; diagnosis: raw_query, suggested concept, confidence, alternatives, status; evaluation: attempt scores, typed_reasoning, evaluated_result, diagnostic_signal). Used by item detail.

- [ ] **Step 2:** `service.py` functions:
  - `list_queue(session, *, status, artifact_type, trigger, subject, limit, offset) -> (items, total)` — filter, order by `priority desc, created_at asc`.
  - `get_item_detail(session, item_id) -> dict` — item + `resolve_artifact(...)` + actions.
  - `approve(session, item, reviewer, notes)` / `reject(session, item, reviewer, reason)` / `correct(session, item, reviewer, fields, notes)` — set status, stamp `first_reviewed_at`/`resolved_at`/`reviewer_id`, append `AiReviewAction`. `correct` calls `CORRECTORS[item.artifact_type]` and stores before/after. `reject` records the rejection (hide enforced in Task 11).
  - `create_learner_flag(session, *, artifact_type, artifact_id, learner, reason)` — calls `flag_artifact(..., extra_reason="learner_flag", learner_id=learner.id, summary=...)`, then appends a `flag` action with `notes=reason` to the resulting open item.

---

### Task 9: Metrics

**Files:** Create `app/modules/review/metrics.py`

- [ ] **Step 1:** `compute_metrics(session) -> dict`:
  - `reviewed` = items with status in `{approved,rejected,corrected}`; `correction_rate = corrected/reviewed`; per `artifact_type` breakdown; `approval_rate`, `rejection_rate`.
  - `trigger_precision`: for each reason in `trigger_reasons`, of resolved items containing it, share ending `corrected` or `rejected`.
  - `backlog`: `open` count + oldest `created_at` age (days).
  - 14-day daily time series of `reviewed` and `corrected` counts (group by `date(resolved_at)`).

---

### Task 10: Router + registration

**Files:** Create `app/modules/review/router.py`; modify `app/api/v1/router.py`

- [ ] **Step 1:** `router.py` endpoints (teacher-gated except `/flag`):

```python
router = APIRouter(prefix="/review")

@router.get("/queue", response_model=ReviewQueueResponse)         # get_current_teacher
@router.get("/items/{item_id}", response_model=ReviewItemDetail)  # get_current_teacher
@router.post("/items/{item_id}/approve", response_model=ReviewItemSummary)  # teacher
@router.post("/items/{item_id}/reject", response_model=ReviewItemSummary)   # teacher
@router.post("/items/{item_id}/correct", response_model=ReviewItemDetail)   # teacher
@router.post("/flag", response_model=ReviewItemSummary)            # get_current_account (learner)
@router.get("/metrics", response_model=ReviewMetricsResponse)     # get_current_teacher
```

Each handler: `Depends(get_session)` + the right account dependency; 404 when item missing.

- [ ] **Step 2:** In `app/api/v1/router.py` add:
`from app.modules.review import router as review_router` and `api_router.include_router(review_router.router, tags=["review"])`.

- [ ] **Step 3:** Verify app boots: `python -c "from app.main import app; print([r.path for r in app.routes if 'review' in r.path])"` → lists the 7 review paths.

---

### Task 11: Wire flagging hooks into producers + reject-hide

**Files:** Modify `app/modules/pretests/generation_service.py`, `app/modules/learning/service.py`, `app/modules/learning_goal_resolution/service.py`, the attempt-evaluation persist site (pretests/posttests answer flow), and the question reuse read path.

- [ ] **Step 1 (questions):** After a question pack + questions are persisted, for each question call:

```python
from app.modules.review.flagger import flag_artifact
flag_artifact(
    session, artifact_type="question", artifact_id=question.id,
    confidence=None,
    signals={"generation_source": pack.generation_source,
             "validator_failed": getattr(question, "_validator_failed", False)},
    subject=subject_code, concept_id=question.concept_id, summary=question.prompt[:160],
)
```

- [ ] **Step 2 (diagnosis):** After a `LearningGoalResolution` is committed:

```python
flag_artifact(session, artifact_type="diagnosis", artifact_id=resolution.id,
    confidence=resolution.confidence,
    signals={"status": resolution.status},
    subject=resolution.subject_code, concept_id=resolution.suggested_concept_id,
    learner_id=resolution.user_id, summary=resolution.raw_query[:160])
```

- [ ] **Step 3 (evaluation):** After an attempt's `reasoning_score`/`evaluated_result` are set & committed:

```python
flag_artifact(session, artifact_type="evaluation", artifact_id=attempt.id,
    confidence=attempt.reasoning_score,
    signals={"diagnostic_signal": attempt.diagnostic_signal,
             "structured_parse_ok": attempt.evaluation_metadata_json.get("structured_parse_ok", True)},
    learner_id=session_obj.user_id, summary=attempt.typed_reasoning[:160])
```

- [ ] **Step 4 (reject-hide):** In the path that *reuses* generated questions across sessions, exclude artifacts with a rejected review item:

```python
rejected_ids = select(AiReviewItem.artifact_id).where(
    AiReviewItem.artifact_type == "question", AiReviewItem.status == "rejected")
query = query.where(AssessmentQuestion.id.notin_(rejected_ids))
```

(If generated questions are strictly per-session and never reused, document that reject is recorded only — no read-path change needed — and skip the query edit.)

- [ ] **Step 5:** Boot check: `python -c "from app.main import app"` (no import errors).

---

## PHASE 2 — Flutter teacher mode

> Detailed code for these tasks is finalized after reading `ui-wicara-asean-fe/lib` patterns (`ApiClient`, an existing repository, a controller, `onGenerateRoute`). Each task below names files + responsibility; the implementer mirrors the existing repository/controller/page conventions exactly.

### Task 12: Review API repository
**Files:** Create `lib/data/api_review_repository.dart`
- [ ] Methods over `ApiClient`: `fetchQueue({status, type, trigger})`, `fetchItem(id)`, `approve(id, notes)`, `reject(id, reason)`, `correct(id, fields)`, `fetchMetrics()`, `flag(type, id, reason)`. Mirror `ApiPretestRepository` JSON/error handling.

### Task 13: Review models + controller
**Files:** Create `lib/models/review_item.dart`, `lib/controllers/review_controller.dart`
- [ ] `ReviewItem`/`ReviewItemDetail`/`ReviewMetrics` from JSON. `ReviewController extends ChangeNotifier` exposing queue, selected item, metrics, loading/error, and actions delegating to the repository, calling `notifyListeners()`.

### Task 14: Role gating + teacher entry
**Files:** Modify the home page + app routing (`lib/.../app.dart` / home page)
- [ ] Read `account.role` from `/me`; if `teacher`, show a "Teacher review" entry that routes to `ReviewQueuePage`. Add routes `review_queue`, `review_detail`, `review_metrics` to `onGenerateRoute`.

### Task 15: ReviewQueuePage
**Files:** Create `lib/pages/review_queue_page.dart`
- [ ] List with filter chips (status/type/trigger), each row showing summary + trigger badges + confidence + priority; tap → detail. Header link to metrics. This is the "show when teachers need to review" surface.

### Task 16: ReviewItemDetailPage
**Files:** Create `lib/pages/review_item_detail_page.dart`
- [ ] Render artifact by `artifact_type` (question/diagnosis/evaluation), show AI confidence/triggers/metadata, and approve / reject (reason) / correct (inline edit form per type) actions; on success pop + refresh queue.

### Task 17: ReviewMetricsPage
**Files:** Create `lib/pages/review_metrics_page.dart`
- [ ] Cards: overall correction rate, per-type rates, approval/rejection, backlog; simple bar/list for trigger precision and the 14-day series.

### Task 18: Learner "flag this" affordance
**Files:** Modify pretest/diagnosis/evaluation result widgets
- [ ] Small "Flag as wrong" button → `ApiReviewRepository.flag(type, id, reason)`; confirm toast. Non-blocking; learner continues regardless.

---

## Implementation status (2026-06-20)

**Backend — DONE & validated** (`python -c "import app.main"` clean; 7 `/api/v1/review/*` routes registered):
Tasks 1–10 complete. Task 11 hooks wired in `learning_goal_resolution/service.py` (diagnosis ×2 commit
sites), `pretests/adaptive_service.py` (evaluation, after attempt flush), and
`pretests/generation_service.py` (questions, both builders) via `enqueue_flag` (independent session →
transaction-safe). Reject-hide (11.4) is a no-op by design: generated questions are per-session (the reuse
query is scoped to `session_id`), so there is no cross-learner reuse path to suppress — rejection is
recorded (status + audit + metrics). Both tables were applied to live Supabase via the Management API
(`CREATE TABLE IF NOT EXISTS`), since the repo's Alembic chain has a pre-existing duplicate revision
(`20260518_0016_weekly_snapshots` declared in two files) that blocks `alembic upgrade head`. The migration
file `20260620_0017_ai_review.py` is the source of truth once that duplicate is removed.

**Frontend — DONE & analyzes clean** (`dart analyze lib/src/features/review` → No issues found):
`lib/src/features/review/` with `domain/review_models.dart` (+ abstract `ReviewRepository`),
`data/api_review_repository.dart`, `application/review_controller.dart`, and
`presentation/{review_queue_page, review_item_detail_page, review_correction_sheet, review_metrics_page,
review_widgets}.dart`. Wired through `main.dart` → `WicaraApp` → `AppHomePage`; a role-gated
"Teacher review" FAB appears on home when `/api/v1/me` reports `role == teacher`.

**Task 18 (learner "flag this") — DONE.** Reusable `FlagReviewButton`
(`review/presentation/flag_review_button.dart`) that self-hides unless the artifact id is a real backend
UUID (`canFlag`), so it appears on backend-backed items and stays hidden for offline-generated ones. Wired
into the posttest/daily-evaluation question view (`_DailyEvaluationQuestionPage`, real backend question ids)
and the pretest question view (`_QuestionStage`, shows on the online path). Backend `create_learner_flag`
validated live: produces `trigger_reasons=['learner_flag']` + a `flag` audit action with `learner_id` set.

**Evaluation flagging — DONE.** Learners can also flag the AI's *reasoning evaluation* (artifact_type
`evaluation`) via a non-blocking post-submit SnackBar ("Scoring off?") on the daily-eval and posttest answer
flows, using the backend `attemptId`. Shared `promptAndFlag()` / `canFlagArtifact()` helpers in
`flag_review_button.dart` back both the button and the SnackBar action. Validated live against a real
`assessment_attempts` row: flag created + the teacher-detail resolver loaded the real attempt
(`diagnostic_signal='concept_gap_likely'`). All review tables verified clean (0 rows) afterward.

**Known trade-off:** `enqueue_flag` runs a synchronous DB write per artifact (1 round-trip for
diagnosis/evaluation, 3 during pack generation). It never gates or rolls back generation, but adds small
latency; moving it to a FastAPI BackgroundTask / queue is the clean next step.

## Self-Review (completed)

- **Spec coverage:** (1) "show when review needed" → Tasks 6,10,15. (2) dashboard/correction workflow → Tasks 7,8,10,16. (3) measure correction need → Tasks 9,17. Role/non-blocking → Tasks 1,2,6,11. ✓
- **Placeholder scan:** Phase 1 has complete code. Phase 2 intentionally defers per-widget Flutter code until `lib/` patterns are read (noted explicitly), not silent TODOs. ✓
- **Type consistency:** `flag_artifact`, `AiReviewItem`, `CORRECTORS`, `resolve_artifact`, `compute_metrics`, `get_current_teacher`, `resolve_role` names used consistently across tasks. ✓
- **Non-blocking invariant:** flagger wrapped in try/except + commit/rollback, called after persistence. ✓
