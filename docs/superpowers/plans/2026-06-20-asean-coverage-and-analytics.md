# ASEAN Coverage & Long-term Insights — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Activate 5 subjects (math + physics/chemistry/biology/science), add 4 ASEAN languages (ms/vi/th/fil), and add a long-term analytics layer — across the FastAPI backend and Flutter app.

**Architecture:** Subjects = data seed from the existing Kurikulum Merdeka graph (no schema change) + dynamic onboarding. Languages = a supported-language registry + a `concept_translations` table with on-demand cached AI translation + native AI generation + Flutter ARB localization. Analytics = a compute-on-read `analytics` module over existing snapshots/concept-states/attempts + a Flutter Insights surface.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, Supabase Postgres; Flutter (ChangeNotifier + repository pattern), `flutter_localizations`/gen-l10n.

**Build order:** Phase 1 Subjects → Phase 2 Analytics → Phase 3 Languages. Tests not required to run (per user); validate via import/compile/`dart analyze` + live Supabase checks through the Supabase MCP.

---

## PHASE 1 — Subjects

### Task 1.1: Seed the full Kurikulum Merdeka graph into Supabase
**Files:** Reuse `app/modules/curriculum/seed.py` (`seed_curriculum`, `load_kurikulum_merdeka_seed_data`), `app/modules/curriculum/kurikulum_merdeka.py` (aliases).
- [ ] Verify `seed_curriculum` upserts subjects/concepts/edges idempotently from `data/wicara_kurikulum_merdeka_graph_complete.json` (391 nodes/521 edges; matematika/ipa/kimia/biologi/fisika/ipas).
- [ ] Run it against the live DB (`python -m app.modules.curriculum.seed` or a small runner that calls `seed_curriculum(session, graph_path)`); confirm canonical codes math/physics/chemistry/biology/science via aliases.
- [ ] Verify via Supabase MCP: `SELECT s.code, count(c.*) FROM subjects s LEFT JOIN knowledge_concepts c ON c.subject_id=s.id GROUP BY s.code` shows all 5 subjects populated.

### Task 1.2: Dynamic onboarding subjects (Flutter)
**Files:** `lib/src/features/onboarding/domain/onboarding_options.dart`, the onboarding subjects controller/page, `lib/src/features/curriculum/data/api_curriculum_repository.dart`.
- [ ] Fetch active subjects from the curriculum API; build `SubjectPreferenceOption` list dynamically with an icon/tint map keyed by subject code + neutral fallback.
- [ ] Extend `OnboardingCopy.subjectLabel` for new codes (physics/chemistry/biology/science) in en/id.

### Task 1.3: Subject scoping verification
**Files:** `app/modules/pretests/graph_scope_builder.py`, `generation_service.py`, `learning_goal_resolution/*`.
- [ ] Confirm graph-scope filters by the selected subject's concept set; add the subject name to generation prompt context. Canvas/evidence stays optional. Smoke a goal resolution for a physics concept.

---

## PHASE 2 — Analytics

### Task 2.1: Backend analytics module
**Files:** Create `app/modules/analytics/__init__.py`, `service.py`, `schemas.py`, `router.py`; register in `app/api/v1/router.py`.
- [ ] `service.compute_overview(session, user)` — cross-subject mastery (avg `learner_concept_states.mastery_score` grouped by subject), gaps, attempt counts, last-active.
- [ ] `service.compute_trends(session, user, period)` — roll up `weekly_report_snapshots` (and attempts) to monthly/all-time mastery & score series.
- [ ] `service.compute_velocity(session, user)` — active days, attempts/week, time-to-mastery, current/longest streak from `assessment_attempts.submitted_at`.
- [ ] `service.compute_at_risk(session, user)` — overdue `next_review_at` + low `confidence_score` + forgetting-curve projection → prioritized list.
- [ ] Endpoints (learner-gated via `get_current_account`): `GET /analytics/overview`, `/trends?period=month|all`, `/velocity`, `/at-risk`.
- [ ] Verify app imports and routes register.

### Task 2.2: Flutter Insights surface
**Files:** Create `lib/src/features/analytics/{data/api_analytics_repository.dart, domain/analytics_models.dart, application/analytics_controller.dart, presentation/insights_page.dart}`; wire into home Progress tab + `WicaraApp`/`main.dart`.
- [ ] Repository over `ApiClient` for the 4 endpoints; models from JSON; controller (ChangeNotifier).
- [ ] Insights UI: multi-period trend chart, cross-subject mastery bars, velocity/streak cards, at-risk list with review deep-links.
- [ ] `dart analyze` clean.

---

## PHASE 3 — Languages

### Task 3.1: Supported-language registry (backend)
**Files:** `app/core/language.py`.
- [ ] Add `SUPPORTED_LANGUAGES = {"en","id","ms","vi","th","fil"}` + alias map; `normalize_language_code` returns one of them (fallback en); `language_display_name` returns endonyms. Keep `is_indonesian_language` for back-compat.

### Task 3.2: concept_translations table + migration
**Files:** Create `app/modules/curriculum` model addition (or `app/modules/curriculum/translation_models.py`), Alembic migration `app/db/migrations/versions/20260620_0018_concept_translations.py`, register in `env.py`.
- [ ] `ConceptTranslation(id, concept_id FK, lang, title, description, source, created_at, updated_at)`, unique `(concept_id, lang)`.
- [ ] Apply via Supabase MCP `apply_migration` (or Alembic; alembic head is now `20260620_0017_ai_review`).

### Task 3.3: Translation service
**Files:** Create `app/modules/curriculum/translation_service.py`.
- [ ] `localize_concept(session, concept, lang)` → cached row or AI-translate via `ai_client` + persist (source='ai'); fallback to en on failure. `localize_text(session, text, lang, cache_key)` generic helper.
- [ ] Localize concept content in content endpoints by `preferred_language`.

### Task 3.4: Native AI generation language
**Files:** `app/modules/learning_goal_resolution/prompt_builder.py` and other generation prompts.
- [ ] Pass full language name (`response_language_name`) so questions/tutoring generate natively in ms/vi/th/fil.

### Task 3.5: Flutter ARB localization
**Files:** `l10n.yaml`, `lib/l10n/app_en.arb` + `app_id/ms/vi/th/fil.arb`, `pubspec.yaml` (`flutter_localizations`, `generate: true`), `wicara_app.dart` (localizationsDelegates/supportedLocales), onboarding language picker.
- [ ] Configure gen-l10n; migrate main-surface copy (onboarding/home/pretest/review) to ARB keys for en/id; add ms/vi/th/fil ARBs (AI-translated), English fallback.
- [ ] Language picker lists 6 languages; sets `preferred_language`.
- [ ] `dart analyze` clean; `flutter gen-l10n` succeeds.

---

## Self-Review
- **Spec coverage:** Subjects → 1.1–1.3; Languages → 3.1–3.5; Analytics → 2.1–2.2. concept_translations table → 3.2. ✓
- **Sequencing:** Subjects → Analytics → Languages, matches spec. ✓
- **Scope honesty:** ARB migration limited to main surfaces (3.5), English fallback — matches spec scope note. ✓
- **Validation:** import/compile + `dart analyze` + Supabase MCP checks; tests not required to run.
