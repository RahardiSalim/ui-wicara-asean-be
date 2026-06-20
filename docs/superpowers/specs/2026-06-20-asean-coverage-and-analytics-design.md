# ASEAN Coverage & Long-term Insights — Design Spec

**Date:** 2026-06-20
**Feature:** #3 — Expand language and curriculum coverage + long-term analytics
**Status:** Approved design, ready for implementation plan

## Goal

Broaden WICARA from a bilingual, math-only tutor into a multi-subject, multi-language
ASEAN learning platform with long-term progress insight:

1. **More ASEAN languages** — add Malay (`ms`), Vietnamese (`vi`), Thai (`th`),
   Filipino/Tagalog (`fil`) alongside English (`en`) and Indonesian (`id`).
2. **More subjects** — activate Physics, Chemistry, Biology, and Science (integrated
   IPA/IPAS) beyond Mathematics.
3. **Long-term analytics** — multi-period trends, cross-subject overview, learning
   velocity & streaks, and at-risk/retention insight.

Delivered as **one spec, three loosely-coupled modules**, built in sequence:
**Subjects → Analytics → Languages** (subjects make analytics multi-subject; languages
is the most invasive and is isolated last).

## Current-state findings (verified)

- **Languages:** binary `en`/`id`. `app/core/language.py::normalize_language_code` collapses
  everything to those two; `is_indonesian_language()` is used widely. Flutter UI copy is
  hardcoded per language via `OnboardingCopy.forLanguage(preferredLanguage)` with a boolean
  `isIndonesian` (`lib/src/features/onboarding/domain/onboarding_copy.dart`). Concept content
  is bilingual via `KnowledgeConcept.id_desc` / `en_desc`. AI prompts already take a
  `response_language` (`learning_goal_resolution/prompt_builder.py`).
- **Subjects:** `Subject` / `KnowledgeConcept` / `ConceptEdge` models support any subject.
  The full Kurikulum Merdeka graph
  (`app/modules/curriculum/data/wicara_kurikulum_merdeka_graph_complete.json`, 953 KB) holds
  **391 nodes / 521 edges** across **matematika (157), ipa (53), kimia (51), biologi (49),
  fisika (41), ipas (40)**. `seed.py::seed_curriculum` → `kurikulum_merdeka.load_kurikulum_merdeka_seed_data(graph_path)`
  already loads the whole graph; aliases (`SUBJECT_ALIASES`, `SUBJECT_LABEL_EN`,
  `SUBJECT_DISPLAY_ORDER`) map graph codes to canonical subject codes. Only math is currently
  live in the DB. Onboarding options are hardcoded (`onboarding_options.dart`).
- **Analytics:** `weekly_report_snapshots`, `learner_concept_states` (`mastery_score`,
  `confidence_score`, `next_review_at`, `last_evaluated_at`), and `assessment_attempts`
  (`submitted_at`, scores). Reports are computed ad-hoc in `learning/service.py`
  (`get_latest_weekly_report`, `get_weekly_report`) with snapshot caching. The frontend
  already shows a weekly `weeklyTimeline` and a `RetentionForecast`. No dedicated long-term
  / cross-subject analytics layer exists.

---

## Module 1 — Subjects

**Schema:** none (existing models suffice).

1. **Seed the full graph.** Run `seed_curriculum` against the complete graph JSON to load all
   six subjects' concepts + edges into Supabase, mapping graph subject codes to canonical
   codes via the existing aliases: `matematika→math`, `fisika→physics`, `kimia→chemistry`,
   `biologi→biology`, `ipa`+`ipas→science`. Idempotent: seeding upserts by `(subject, code)`
   and re-points edges; existing math data is preserved. Reserved empty subject rows
   (physics/chemistry/biology in `seed_data.py`) are reconciled with the seeded rows.
2. **Dynamic onboarding subjects.** Flutter fetches active subjects from the curriculum API
   (the existing `/curriculum` subjects listing) and renders them instead of the hardcoded
   `onboardingSubjectOptions`. A small icon/tint map keys off subject code with a neutral
   fallback for unknown codes. `OnboardingCopy.subjectLabel` is extended for the new codes.
3. **Subject scoping in AI flows.** Confirm pretest generation, learning-goal resolution, and
   tutoring scope to the selected subject's sub-graph (graph-scope builder already filters by
   concept set). Add the subject name to generation prompts for context. Canvas/evidence
   remains optional, so non-math subjects work without a math working surface.

**Success:** a learner can onboard into Physics/Chemistry/Biology/Science, resolve a goal,
take an adaptive pretest, and get tutoring in that subject.

---

## Module 2 — Languages

**Chosen strategy:** AI authors content natively in the target language; curated concept
descriptions are AI-translated on demand and cached; UI strings use a bundled localization
layer with English fallback.

### Backend

- **Supported-language registry.** Generalize `app/core/language.py` to a registry
  `{"en","id","ms","vi","th","fil"}` with an alias map and English fallback.
  `normalize_language_code` returns one of the six. `language_display_name` returns the
  endonym (e.g. `vi → "Tiếng Việt"`). `is_indonesian_language` is retained for back-compat but
  call sites that branch binary on it are migrated to language-aware lookups where it matters.
- **Concept translations table** (new, **chosen over a JSON column**):
  `concept_translations(id PK, concept_id FK, lang, title, description, source 'curated'|'ai',
  created_at, updated_at)` with a unique `(concept_id, lang)`. `id_desc`/`en_desc` remain as
  seed/fallback (treated as the `id`/`en` rows conceptually).
- **`translation_service.localize_concept(session, concept, lang)`** — returns the cached
  translation row, else AI-translates via `ai_client` (source = English/Indonesian text →
  target language), persists to `concept_translations` (source=`ai`), and returns it.
  Best-effort: on translation failure, fall back to English. A generic helper
  `localize_text(text, target_lang, cache_key)` backs reusable string translation with a small
  cache keyed by source hash + target lang.
- **AI generation language.** Pass the full language name into existing generation prompts
  (`response_language` → `response_language_name`) so questions/tutoring are produced natively
  in `ms/vi/th/fil`.
- Content-returning endpoints localize concept text to the learner's `preferred_language`.

### Frontend

- **UI strings via `flutter_localizations` + ARB (gen-l10n)** (**chosen over backend-served
  strings** — the app is offline-first). English is the fallback locale.
  - Migrate the main user-facing surfaces (onboarding, home, pretest, review) from hardcoded
    `OnboardingCopy` to ARB keys for `en`/`id`.
  - Add `ms/vi/th/fil` ARBs (AI-assisted translations; human QA deferred). Missing keys fall
    back to English.
  - *Scope note:* round one does not migrate literally every string — unmigrated strings stay
    on their current path and render English where keyed.
- **Language picker** in onboarding lists all six languages; selection sets
  `preferred_language`, which drives backend-localized content.

*Most invasive module; the ARB migration is the bulk of the effort.*

---

## Module 3 — Long-term Analytics

**Storage:** **compute-on-read** (chosen over a precomputed monthly table) from
`weekly_report_snapshots` + `learner_concept_states` + `assessment_attempts`; per-learner data
volumes are small. Weekly snapshots provide the weekly grain; the service rolls them up.

### Backend — new `analytics` module (`service.py` + `router.py`)

- **Multi-period trends** — mastery & score series aggregated to monthly and all-time from the
  weekly snapshots / attempts.
- **Cross-subject overview** — for each subject the learner studies: average mastery, open
  gaps, attempt count, last-active — side by side.
- **Velocity & streaks** — active days, attempts-per-week trend, time-to-mastery per concept
  (first-seen → mastered via `learner_concept_states`/attempt timestamps), current & longest
  streak from `assessment_attempts.submitted_at`.
- **At-risk / retention** — concepts with overdue `next_review_at` or low `confidence_score`,
  plus a forgetting-curve projection (reuse the existing retention-forecast logic) → a
  prioritized "review now" list.
- **Endpoints:** `GET /api/v1/analytics/overview`, `GET /api/v1/analytics/trends?period=month|all`,
  `GET /api/v1/analytics/velocity`, `GET /api/v1/analytics/at-risk`. Learner-gated via the
  existing `get_current_account`.

### Frontend — "Insights" surface

Extends the Progress tab (`app_home_page.dart`) with: a multi-period trend chart, cross-subject
mastery bars, velocity/streak cards, and an at-risk list whose items deep-link into review
(daily evaluation / posttest). New `ApiAnalyticsRepository` + models following the existing
repository pattern.

---

## Data model summary

| Change | Table | Notes |
|---|---|---|
| **New** | `concept_translations` | `(concept_id, lang, title, description, source)`, unique `(concept_id, lang)` |
| Unchanged | `subjects`, `knowledge_concepts`, `concept_edges` | seeded with all 6 subjects |
| Unchanged | `weekly_report_snapshots`, `learner_concept_states`, `assessment_attempts` | analytics computed on read |

Schema delta is **one new table** (`concept_translations`) via a new Alembic migration; the
multi-subject seed is a data load, not a schema change.

## Sequencing

1. **Subjects** — seed + dynamic onboarding + per-subject verification.
2. **Analytics** — analytics module + Insights UI (now multi-subject).
3. **Languages** — registry + translations table/service + AI-language generation + ARB UI.

Each module is independently shippable and testable.

## Out of scope (round one)

Human-reviewed translation QA; ARB migration of every screen; cohort/social comparisons; RTL
(none of the six languages require it); precomputed analytics materialization.

## Testing notes

Per user preference tests are not required to run, but units stay testable: the language
registry and `localize_concept` are pure given inputs + cache; the seed is idempotent and
verifiable by subject/edge counts; analytics functions compute deterministically from the three
source tables. Live verification (subject seed counts, a sample translation, analytics
endpoints) can be done against Supabase via the connected MCP.
