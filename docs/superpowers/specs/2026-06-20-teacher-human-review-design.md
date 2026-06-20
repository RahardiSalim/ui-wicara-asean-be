# Teacher / Human-in-the-Loop Review — Design Spec

**Date:** 2026-06-20
**Feature:** #4 — Add clearer teacher / human involvement
**Status:** Approved design, ready for implementation plan

## Goal

Make human (teacher) involvement in WICARA's AI a first-class, visible part of the
product:

1. **Show when teachers need to review or correct AI outputs.**
2. **Provide a teacher review dashboard / human-correction workflow.**
3. **Measure how often human correction is actually needed.**

## Guiding principle — review is asynchronous and NON-BLOCKING

The single most important constraint: **review never blocks AI generation or the
learner.** Every AI output ships to the learner immediately. A parallel review layer
flags items, lets teachers approve / reject / correct them after the fact, and
corrections take effect *going forward*. AI generation must never wait for teacher
approval. Flagging is best-effort: a failure in the review layer must never break a
learner-facing flow.

## Scope

### In scope — three AI output types are reviewed

| # | Output | Source table(s) | "Confidence" source |
|---|--------|-----------------|---------------------|
| 1 | **Generated questions** | `assessment_questions` (+ `assessment_options`, `assessment_question_packs`) | `llm_metadata_json`, `generation_source` (no direct numeric confidence — see flagger) |
| 2 | **Learning-goal diagnoses** | `learning_goal_resolutions` | `confidence` (float 0–1), `status` |
| 3 | **Reasoning evaluations** | `assessment_attempts` | `reasoning_score`, `evaluated_result`, `evaluation_metadata_json` |

### Out of scope (round one)

- Reviewing tutor chat replies (`workspace_events`) and learning-video specs (`media_artifacts`).
- Auto-regeneration on reject (reject = hide only for now).
- Real-time push notifications to teachers (queue is pull/polled).
- Multi-teacher assignment / claim locking (single shared queue for the demo).

## Architecture overview

```
AI producing service (questions / diagnosis / evaluation)
  | persists artifact  (UNCHANGED, ships to learner immediately)
  | then best-effort →
  v
review.flagger.evaluate(artifact_type, artifact_id, signals)   # never raises to caller
  | creates/updates one OPEN ai_review_items row if any trigger fires
  v
ai_review_items  (central queue + workflow state + metrics backbone)
  ^
  | teacher reads queue / item detail (artifact content resolved on demand)
  | teacher approve / reject / correct
  v
review.service + review.correctors.*    # correction writes BACK to source table
  |
  +→ ai_review_actions  (audit log: action, notes, before/after snapshot)  → metrics
```

### Why a central queue + write-back (chosen approach)

- **(A) Central polymorphic queue only** — unified, but corrections never reach learners.
- **(B) Review columns on each of the 3 tables** — DB-native FK, but 3× schema churn, no
  unified queue, scattered metrics, does not scale to new artifact types.
- **(C) Hybrid — central queue + write-back (CHOSEN)** — one queue table drives the
  workflow and metrics; corrections write back into the real domain tables so learners
  actually receive the fix; existing AI tables are left untouched. Best of both.

## Data model

Two new tables. **No *schema* changes to existing AI tables** — corrections update *data*
in those tables (via correctors) and rejection hides via the queue, but no columns are
added to `assessment_questions`, `learning_goal_resolutions`, or `assessment_attempts`.
Added via a new Alembic migration (`app/db/migrations/versions/`), applied with
`alembic upgrade head` against the Supabase `DATABASE_URL`.

### `ai_review_items` — the queue

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `artifact_type` | str(32) | `'question' \| 'diagnosis' \| 'evaluation'` |
| `artifact_id` | UUID | polymorphic ref to source row (no DB FK) |
| `status` | str(32) | `'open' \| 'approved' \| 'rejected' \| 'corrected'`, default `'open'` |
| `trigger_reasons` | JSON list | subset of `['low_confidence','risk_signal','sampled','learner_flag']` |
| `confidence` | float, null | snapshotted confidence at flag time (per-type, see flagger) |
| `priority` | int | higher = surface first (derived from triggers/confidence) |
| `subject` | str, null | denormalized for filtering |
| `concept_id` | UUID, null | denormalized for filtering |
| `learner_id` | UUID FK user_accounts, null | which learner the output affected |
| `summary` | str, null | short human-readable description for the queue list |
| `reviewer_id` | UUID FK user_accounts, null | teacher who resolved it |
| `created_at` | tz datetime | |
| `first_reviewed_at` | tz datetime, null | first teacher action |
| `resolved_at` | tz datetime, null | approve/reject/correct timestamp |

**Idempotency:** unique partial index on `(artifact_type, artifact_id) WHERE status = 'open'`.
Re-flagging an already-open item appends to `trigger_reasons` instead of inserting.

### `ai_review_actions` — the audit log (powers metrics)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `review_item_id` | UUID FK ai_review_items | |
| `reviewer_id` | UUID FK user_accounts, null | null for system/learner `flag` action |
| `action` | str(32) | `'approve' \| 'reject' \| 'correct' \| 'flag'` |
| `notes` | Text | teacher rationale |
| `before_json` | JSON, null | artifact snapshot before a correction |
| `after_json` | JSON, null | artifact snapshot after a correction |
| `created_at` | tz datetime | |

## Flagging engine — `app/modules/review/flagger.py`

A post-persistence, best-effort hook. Producing services call it **after** they save the
artifact and after it is already on its way to the learner. The whole call is wrapped so
any exception is logged and swallowed — it can never break generation.

```python
def flag_artifact(session, *, artifact_type, artifact_id, signals: dict) -> None:
    # best-effort; logs and returns on any error
```

Rules (all thresholds/percentages configurable in `app/core/config.py`):

| Trigger | Rule |
|---------|------|
| `low_confidence` | per-type confidence `< threshold`. Diagnosis: `confidence`. Evaluation: `reasoning_score`. Question: no native score → treated as low-confidence when `generation_source` is a fallback/deterministic path. |
| `risk_signal` | question validator failed; `generation_source in {fallback_generated, deterministic_fallback}`; `diagnostic_signal == 'misconception_detected'`; `structured_parse_ok == false`; diagnosis `status == 'needs_clarification'`. |
| `sampled` | deterministic sample: `hash(str(artifact_id)) % 100 < sample_pct` (reproducible & testable; not RNG). Per-type `sample_pct`. |
| `learner_flag` | created by the learner `POST /v1/review/flag` endpoint. |

The flagger resolves a per-type `confidence` snapshot and a short `summary`, computes
`priority`, and creates or updates the single open `ai_review_items` row. Each fired
trigger is added to `trigger_reasons` (deduplicated).

### Where the hooks are wired (producing services)

- **Questions:** `app/modules/pretests/generation_service.py` and
  `app/modules/learning/service.py` (posttest/daily question generation) — after the
  question pack is persisted.
- **Diagnoses:** `app/modules/learning_goal_resolution/service.py` — after a resolution is
  persisted.
- **Evaluations:** wherever an `assessment_attempt` gets its `reasoning_score` /
  `evaluated_result` set (pretest/posttest answer-evaluation flow) — after the attempt is
  persisted.

## Teacher role & auth

Today `app/modules/accounts/dependencies.py` hard-codes `role="learner"`. Changes:

1. Read role from Supabase claims (`app_metadata.role` / `user_metadata.role`) in
   `sync_supabase_user`, defaulting to `"learner"` when absent.
2. Config allowlist `TEACHER_EMAILS` in `app/core/config.py` — emails in the list are
   synced as `role="teacher"` (pragmatic for the demo without Supabase dashboard edits).
3. New dependency `get_current_teacher` → returns the account if `role == "teacher"`, else
   `403`.
4. `/v1/me` (`app/api/v1/me.py`) returns `role` so the Flutter app can toggle teacher mode.

## API — new `review` router (`/v1/review/...`)

Registered in `app/api/v1/router.py`. New file `app/modules/review/router.py`.

| Method & path | Auth | Purpose |
|---------------|------|---------|
| `GET /v1/review/queue` | teacher | List items. Filters: `status`, `artifact_type`, `trigger`, `subject`. Sorted by `priority` desc, `created_at` asc. Paginated. |
| `GET /v1/review/items/{id}` | teacher | Item + **resolved artifact content** (question/diagnosis/evaluation rendered) + AI metadata (provider, model, confidence, triggers). |
| `POST /v1/review/items/{id}/approve` | teacher | `status='approved'`, log `approve` action (optional notes). |
| `POST /v1/review/items/{id}/reject` | teacher | `status='rejected'`, `reason` required, log `reject`. Hides artifact from future learners (see "Reject behavior"). |
| `POST /v1/review/items/{id}/correct` | teacher | Body = corrected fields. Writes back to source artifact via corrector, `status='corrected'`, log `correct` with before/after. |
| `POST /v1/review/flag` | learner | Body `{artifact_type, artifact_id, reason}`. Enqueues a `learner_flag` item + `flag` action. |
| `GET /v1/review/metrics` | teacher | Correction-rate dashboard data (below). |

### Reject behavior (hide only)

Rejecting sets the review item to `rejected`. Hiding is enforced **through the queue, not a
new column**: learner-facing read paths that could *reuse* an artifact (primarily generated
questions promoted/reused across sessions) exclude any `artifact_id` that has a `rejected`
`ai_review_items` row (a subquery against the queue). Diagnoses and evaluations are
single-learner artifacts already shown, so "hide" is a no-op for them beyond the recorded
rejection. No regeneration is triggered in round one.

## Correctors — `app/modules/review/correctors.py`

One small write-back function per artifact type. Each takes the corrected payload,
snapshots `before`, applies the edit to the source table, and returns `after`.

- `correct_question` — edit `prompt`, option `text`/`is_correct`, `expected_reasoning`,
  `rubric_json`.
- `correct_diagnosis` — override `suggested_concept_id`, set `status='confirmed'`.
- `correct_evaluation` — override `reasoning_score` / `diagnostic_signal` / feedback on the
  attempt.

## Metrics — "how often human correction is needed"

Computed from `ai_review_items` + `ai_review_actions`. `GET /v1/review/metrics` returns:

- **Correction rate** = `corrected / reviewed` — overall, per `artifact_type`, and a recent
  time series (e.g. last 14 days).
- **Approval rate** and **rejection rate**.
- **Trigger precision** — for each trigger, share of its items that ended in `corrected` or
  `rejected` (i.e., the trigger caught a real problem) vs `approved`. Lets thresholds be
  tuned.
- **Backlog** — count of `open` items and age of the oldest.

## Frontend — Flutter teacher mode (inside existing app)

Role-gated, following the existing repository/controller pattern (`ApiClient`,
`ChangeNotifier` controllers, `onGenerateRoute`).

- **Data:** `ApiReviewRepository` (queue/detail/approve/reject/correct/metrics/flag) +
  `ReviewController`.
- **Routing/role:** `/me` exposes `role`; if `teacher`, show a "Teacher" entry on home.
- **Screens:**
  - `ReviewQueuePage` — list with trigger + confidence badges and filters. This is the
    "show when teachers need to review" surface.
  - `ReviewItemDetailPage` — renders the artifact by type, shows AI confidence / triggers /
    metadata, with approve / reject / inline-correct forms.
  - `ReviewMetricsPage` — correction-rate cards + per-type / per-trigger breakdown.
- **Learner side:** a small "flag this" affordance on questions, diagnoses, and evaluation
  results → `POST /v1/review/flag`.

## Module layout (backend)

```
app/modules/review/
  __init__.py
  models.py        # AiReviewItem, AiReviewAction
  schemas.py       # request/response Pydantic models
  flagger.py       # flag_artifact() + trigger rules
  correctors.py    # per-type write-back
  service.py       # queue/detail/approve/reject/correct + resolve artifact content
  metrics.py       # correction-rate computations
  router.py        # FastAPI endpoints
```

## Testing notes

The user explicitly does not require running tests this round, but the design keeps units
testable: the flagger is pure given a session + signals; correctors take/return JSON
snapshots; metrics compute from the two tables. Trigger rules are deterministic
(sampling uses a hash, not RNG).

## Out-of-scope / future

- Tutor-reply and video-spec review.
- Auto-regeneration on reject.
- Real-time teacher notifications and multi-teacher claim/assignment.
