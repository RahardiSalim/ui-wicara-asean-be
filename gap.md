# Workspace Feature — Backend ↔ Mobile Gap Analysis

**Scope:** the Workspace (5E tutoring chat) feature only.

**Code reviewed**
- Backend: `ui-wicara-asean-be/app/api/v1/workspaces.py`, `app/modules/workspaces/{service,schemas,models,tutor,mastery}.py`, `app/modules/evidence/*`, plus the posttest/learning/inputs touchpoints.
- Mobile: `ui-wicara-asean-mobile/lib/src/features/workspace/**`, `lib/src/app/wicara_app.dart`, `lib/main.dart`, `lib/src/features/home/presentation/app_home_page.dart`.

**Status: 22 of 23 gaps fixed; 1 closed as by-design.** See the [status table](#status-summary) at the end.

**Verdict (original).** The wire contract was in good shape — almost everything the backend emitted parsed cleanly on mobile. The breakage was elsewhere: the 5E progression state machine produced states the UI was built to react to but that the backend could never emit, several backend capabilities had no client path, and a handful of error paths returned 500 or dead-ended the learner.

> **Correction to the first version of this report.** GAP-08 originally claimed "there is no image-upload endpoint on the backend at all." That was wrong. `POST /api/v1/evidence/image-assets/upload` exists in `app/modules/evidence/router.py:25` and is mounted at `app/api/v1/router.py:17`; the original grep only covered `app/api/v1/` and missed routers defined under `app/modules/`. The gap was real but narrower than written — the endpoint existed, but nothing called it, the tutor had no multimodal path, and there was no way to read an image back. See GAP-08 below.

Severity key: **P0** = learner-visible breakage / data loss / 500. **P1** = feature is dead or silently degraded. **P2** = correctness/robustness debt.

---

## 1. The 5E phase state machine

### GAP-01 (P0) — `phase_transition_pending` could never be `true`, so the "Next phase" button was permanently dead ✅ Fixed

`service.py` computed `phase_transition_pending = phase_ready`, then immediately auto-advanced when `phase_ready and current_turns >= min_turns`, and `_advance_metadata_to_phase` reset pending to `False`. Since `_DEFAULT_PHASE_MIN_TURNS` is `1` for every phase and the turn counter is incremented earlier in the same request, the guard always held — so the only surviving path to `pending = true` was unreachable.

**Decision taken: learner-confirmed advance.** The UI (footer button, stepper pending state, the `advance-phase` endpoint, and an existing mobile test) is all built for it, and removing auto-advance also fixes GAP-02 for free. The auto-advance block is gone; `append_workspace_event` now only flags readiness:

```python
phase_ready = _phase_is_ready(metadata_json, phase=current_phase)
if current_phase != "evaluate":
    min_turns = int(_phase_min_turns(metadata_json).get(current_phase, 1))
    current_turns = _current_phase_turns(metadata_json)
    metadata_json["phase_transition_pending"] = phase_ready and current_turns >= min_turns
```

### GAP-02 (P0) — the tutor message the learner read belonged to the *previous* phase ✅ Fixed

The reply was generated for `current_phase`, then the same request advanced the phase, so the learner read an Engage hook under an "Explore" header. Removing auto-advance (GAP-01) means the phase only changes when the learner confirms, and the next turn opens the new phase cleanly.

### GAP-03 (P0) — remediation loops: evidence was never cleared when a phase was re-entered ✅ Fixed

`_remediate_metadata_to_phase` sent the learner back to `explore`/`elaborate` but left `phase_evidence` intact, so `_phase_is_ready` re-fired from stale evidence and the learner ping-ponged back to `evaluate`.

New `_clear_phase_evidence_from()` drops evidence for the remediation target and every phase after it. Covered by `test_remediation_clears_evidence_so_the_learner_cannot_ping_pong`.

### GAP-04 (P1) — `evaluate` → posttest required three evidence tags *plus* a perfect final turn ✅ Fixed

`_PHASE_REQUIRED_EVIDENCE["evaluate"]` demanded `independent_attempt` **and** `error_analysis` **and** `reflection` as three separate groups, and eligibility additionally required `evaluation_outcome == "passed"` **and** `correctness == "correct"` on the same turn — with every other outcome resetting `posttest_eligible` to `False`.

Now: `independent_attempt` **and** (`error_analysis` **or** `reflection`); eligibility needs `phase_ready and outcome == "passed"` (which already implies correctness); and eligibility is **sticky** — once earned it survives until a remediation explicitly revokes it.

### GAP-05 (P0) — an AI outage left the learner permanently stuck in Engage ✅ Fixed

`_fallback_response` returned no evidence tags and `confidence=0.0`, so no evidence was ever recorded during an outage and the phase never moved — with no signal to the learner.

Three changes: the AI call now retries (`_TUTOR_MAX_ATTEMPTS = 2`) before falling back; the fallback sets `degraded` in the audit, which flows to `TutorResponseRead.degraded` and a new `WorkspaceRead.tutor_degraded`; and mobile renders a `_TutorOfflineBanner` plus a per-turn chip explaining that the phase will not advance until the tutor recovers.

### GAP-06 (P2) — the scaffold contract was stated to the model but never supplied ✅ Fixed

The system prompt referenced "backend scaffold level is 3 or higher" while the payload only carried `hint_level` — the term "scaffold level" appeared nowhere. There is now an explicit per-turn `Scaffold policy:` block naming the current level and what may be revealed at it, and `scaffold_level` is included in the learning context.

### GAP-07 (P2) — hint-level decay was asymmetric ✅ Fixed

The ladder climbed via a convoluted `min(6, max(failures, 3 if failures >= 3 else 0))` — which simplifies exactly to `min(6, failures)` — but decayed by only 1 per success. Now `min(_MAX_HINT_LEVEL, failures)` up, `_HINT_DECAY_PER_SUCCESS` (2) down. Covered by `test_scaffold_unwinds_after_recovery`.

---

## 2. Capabilities the backend had that mobile could not reach

### GAP-08 (P0) — the canvas drawing never reached the tutor ✅ Fixed

**As originally filed this overstated the problem — see the correction above.** The upload endpoint existed; what was missing was everything around it:

- Mobile's `appendEvent` had no image parameter and never sent `image_asset_id`.
- `_handleCanvasSentToChat` shipped only *metadata about* the drawing (`element_count`, canvas dimensions) — the pixels were dropped, while `tutor.py` replied "I saved your canvas snapshot", which was not true in any meaningful sense.
- `tutor.generate_tutor_response` took no image input at all.
- There was no endpoint to read an image back, so a drawing could never be re-rendered in the transcript.
- `image_asset_id` was accepted on workspace events **without any ownership check** — any caller could attach an arbitrary or another user's asset id.

Now, end to end: mobile rasterises via the pre-existing `renderCanvasSnapshotPng`, uploads to `POST /evidence/image-assets/upload`, and attaches the returned id to the event; the backend validates ownership (`_resolve_tutor_image_input`, rejecting foreign ids with 404) and passes the file to the model through the AI client's existing image-input support; a new `GET /evidence/image-assets/{id}/file` serves it back, ownership-checked, for `_WorkspaceImageAttachment` to render. The system prompt now instructs the tutor to read the image and never claim to have seen a drawing when none was supplied.

If the upload fails the turn still sends as text, with the learner told the tutor only received text — a degraded turn beats a lost one.

### GAP-09 (P1) — the canvas bubble disappeared one frame after sending ✅ Fixed

The optimistic canvas entry was replaced when `_chatEntries` was rebuilt from the server, and `_entriesFromEvents` turned `canvas_sent` into a plain text label. Entries now carry an `imageUrl` resolved from the event's `image_asset_id`, so the drawing persists and survives reopening the session.

### GAP-10 (P1) — `tutor_response` and `mastery_update` were parsed and thrown away ✅ Fixed

`appendResultFromJson` fully decoded correctness, misconception status, confidence, scaffold level and the whole mastery update; `_appendWorkspaceEvent` used only `result.workspace`. Both are now retained in state and rendered by `_TutorFeedbackStrip` (correctness, misconception, mastery delta, hint level).

### GAP-11 (P1) — `hintLevel`, `phaseEvidence`, `learningContext` were decoded but unrendered ✅ Fixed

The routing rationale built in `_apply_workspace_context` was invisible. A new `_LearningContextCard` shows the diagnosis reason and the original target, so the learner is told *why* they were routed to a prerequisite module; hint level surfaces in the feedback strip.

### GAP-12 (P2) — event types mobile never sent ✅ Fixed (partially by design)

`media_viewed` is now emitted when playback actually starts, making video engagement measurable. `quiz_answer` and `note` remain unused — the workspace is a free-text chat surface and has no quiz UI, so there is nothing to send them from; the backend support stays for the assessment surfaces that do.

### GAP-13 (P2) — `WorkspaceEvent` dropped four fields ✅ Fixed

`imageAssetId`, `mediaArtifactId`, `inputEventId` and `createdAt` are now decoded, along with `lastImageAssetId` on the session.

### GAP-14 (P2) — `media_generated` events were invisible in the transcript ✅ Fixed

Events with an empty `textPayload` were dropped, so "a video was requested here" left no trace. `media_generated` now renders as a muted `_WorkspaceTranscriptNote`, and image-only events render as an attachment.

---

## 3. Error paths and lifecycle

### GAP-15 (P0) — opening a locked module returned HTTP 500 ✅ Fixed

`create_or_resume_workspace` raised `ValueError` but the route caught only `LookupError`. Now mapped to **409** with the prerequisite message. Covered by `test_opening_a_locked_module_is_a_conflict_not_a_server_error`.

### GAP-16 (P0) — stale local workspace ids survived logout and locked the user out ✅ Fixed

`WorkspaceSessionStore.clearAll()` was never called, so after switching accounts the app posted the previous user's id, got a 404, and hard-blocked that module with no in-app recovery.

Two fixes: `AuthController` gained an `onSignedOut` cleanup hook list, wired in `main.dart` to `workspaceStore.clearAll`; and `_loadWorkspace` now catches a failure on a *cached* id, clears just that pointer via `clearCachedSession`, and retries with a fresh resume.

### GAP-17 (P1) — double-tap send could 500 on a unique-constraint violation ✅ Fixed

Two in-flight appends both read the same `max(event_index)` and the second commit tripped `uq_workspace_events_session_index`.

Fixed on both sides. Backend: the append and video-generation paths take the workspace row with `SELECT … FOR UPDATE`, serialising concurrent appends. (A retry loop was the obvious alternative but is wrong here — the whole append is one transaction, so a rollback would also discard the input-event ledger row and the mastery write.) Mobile: `_sendMessage` returns early while `_isAppendingEvent`, and the composer field and send button are disabled with a spinner.

### GAP-18 (P1) — resuming a finished workspace silently reopened it ✅ Fixed

Auto-resume picked the most recently updated session regardless of status and unconditionally set it back to `active`, undoing the posttest's completion. Auto-resume now excludes `completed` sessions, and an explicitly requested completed session is never demoted. Covered by `test_a_completed_workspace_is_not_resumed_or_reopened`.

### GAP-19 (P2) — the posttest is started twice ⚪ Closed: by design

Re-examined and **not** worth changing. `AdaptivePosttestService.start` calls `_active_posttest_for_goal` first and returns the existing session when there is one, so the second call is literally the *read* half of a get-or-create — not a duplicate creation. The workspace endpoint owns the eligibility transition; home owns presenting it. Restructuring to collapse the two would move the eligibility gate for no behavioural gain. Documented rather than "fixed".

One real sub-issue was addressed: `posttest_trigger.status` staying `"ready"` forever is now accompanied by an explicit `error: None` and, on failure, a `status: "error"` trigger (see GAP-20).

### GAP-20 (P2) — `posttest_trigger` fields mobile parsed that the backend never sent ✅ Fixed

`concept_code`, `concept_title` and `error` were read by the client and never written by the server, so the error path could only ever show a generic fallback string. All three are now populated, and a failed posttest start records a `status: "error"` trigger with the real message before raising.

### GAP-21 (P2) — video polling had no resume and gave up silently ✅ Fixed

Backgrounding, switching sessions, or the 5-minute timeout abandoned the job with no way to re-attach, even though the artifact usually lands. The job id is now retained on timeout and a "Re-attach" action resumes polling instead of paying to regenerate.

### GAP-22 (P2) — `GET /workspaces` had no pagination, no delete, and a hardcoded Indonesian string ✅ Fixed

Added `limit`/`offset` (with `total` and `has_more` in the response), `DELETE /workspaces/{id}` with a confirm flow in the history sheet, and localisation of the empty-preview string. Covered by `test_session_history_paginates_and_supports_deletion`.

### GAP-23 (P2) — the local session store duplicated a server-authoritative list ✅ Fixed

`WorkspaceSessionHistory.workspaceIds` had no reader and was the direct cause of GAP-16. The store is now a single `Map<track+module → activeWorkspaceId>` (with migrations from both legacy formats), and the model moved to the domain layer where both sides can share it.

---

## 4. Test coverage

Backend went from 8 workspace tests to 15. New coverage: locked-module 409, foreign image-asset rejection, history pagination + deletion, resume-after-completion, remediation evidence clearing, evaluate-gate reachability, and scaffold unwind.

Two pre-existing tests (`test_workspace_api.py::test_workspace_events_are_persisted_in_module_timeline` and `test_input_events.py::test_workspace_event_creates_canonical_input_event`) attached fabricated `image_asset_id`s and started failing once ownership validation landed. They were updated to mint a real asset rather than weakening the check — the fact that they passed before is itself the evidence that unvalidated ids were being accepted.

Mobile went from 3 repository tests to 8: canvas upload + asset attachment, the new event id/timestamp fields, degraded-tutor propagation, bounded history pages, and cached-pointer cleanup on delete.

**Not covered:** there is still no widget test for `WorkspaceModulesPage`, which is where most of the P0/P1 UI fixes live. That remains the largest test gap in the feature.

---

## Status summary

| # | Gap | Severity | Status |
|---|---|---|---|
| 01 | `phase_transition_pending` unreachable | P0 | ✅ Fixed — learner-confirmed advance |
| 02 | Tutor reply one phase behind | P0 | ✅ Fixed |
| 03 | Remediation ping-pong | P0 | ✅ Fixed |
| 04 | Evaluate gate unreachable | P1 | ✅ Fixed |
| 05 | AI outage freezes progression | P0 | ✅ Fixed |
| 06 | Scaffold level never supplied | P2 | ✅ Fixed |
| 07 | Asymmetric hint decay | P2 | ✅ Fixed |
| 08 | Canvas never reaches tutor | P0 | ✅ Fixed (finding corrected) |
| 09 | Canvas bubble vanishes | P1 | ✅ Fixed |
| 10 | Tutor/mastery feedback discarded | P1 | ✅ Fixed |
| 11 | Learning context unrendered | P1 | ✅ Fixed |
| 12 | Unused event types | P2 | ✅ `media_viewed` wired; rest by design |
| 13 | Event fields dropped | P2 | ✅ Fixed |
| 14 | `media_generated` invisible | P2 | ✅ Fixed |
| 15 | Locked module → 500 | P0 | ✅ Fixed |
| 16 | Stale ids lock user out | P0 | ✅ Fixed |
| 17 | Concurrent append → 500 | P1 | ✅ Fixed (row lock + UI guard) |
| 18 | Completed workspace reopened | P1 | ✅ Fixed |
| 19 | Posttest started twice | P2 | ⚪ Closed — get-or-create, by design |
| 20 | Unpopulated trigger fields | P2 | ✅ Fixed |
| 21 | Video polling not resumable | P2 | ✅ Fixed |
| 22 | No pagination/delete/i18n | P2 | ✅ Fixed |
| 23 | Redundant local session list | P2 | ✅ Fixed |

### Verification status

- **Backend:** full suite run against a temporary venv — all workspace and posttest tests pass. One unrelated failure (`test_curriculum_api.py::test_get_knowledge_map_returns_mobile_ready_kurikulum_graph`) was confirmed pre-existing by re-running it on a stashed tree.
- **Mobile:** ⚠️ **not compiled or run.** No Flutter/Dart SDK is installed on the machine these changes were made on, so `flutter analyze` and `flutter test` could not be executed. The Dart edits were verified only by structural brace/string balance checking and by auditing every implementer and call site of the changed interfaces (`WorkspaceRepository`, `_WorkspaceChatPanel`, `_WorkspaceFooter`, `_WorkspaceHistorySheet`, `WorkspaceSessionHistory`). **Run `flutter analyze && flutter test` before merging the mobile side.**
