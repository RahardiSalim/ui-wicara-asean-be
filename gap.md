# Workspace Feature — Backend ↔ Mobile Gap Analysis

**Scope:** the Workspace (5E tutoring chat) feature only.

**Code reviewed**
- Backend: `ui-wicara-asean-be/app/api/v1/workspaces.py`, `app/modules/workspaces/{service,schemas,models,tutor,mastery}.py`, plus the posttest/learning/inputs touchpoints it calls.
- Mobile: `ui-wicara-asean-mobile/lib/src/features/workspace/**`, `lib/src/app/wicara_app.dart`, `lib/main.dart`, `lib/src/features/home/presentation/app_home_page.dart`.

**Verdict.** The wire contract (field names, endpoints, JSON shapes) is actually in good shape — almost everything the backend emits parses cleanly on mobile. The breakage is elsewhere: **the 5E progression state machine produces states the UI is built to react to but that the backend can never emit**, several backend capabilities have **no client path at all** (image/canvas upload, mastery feedback, hint level), and a handful of **error paths return 500 or dead-end the learner**. That is the "ngaco" the feature is showing.

Severity key: **P0** = learner-visible breakage / data loss / 500. **P1** = feature is dead or silently degraded. **P2** = correctness/robustness debt.

---

## 1. The 5E phase state machine

### GAP-01 (P0) — `phase_transition_pending` can never be `true`, so the "Next phase" button is permanently dead

`service.py:501-513` computes `phase_transition_pending = phase_ready`, then immediately auto-advances when `phase_ready and current_turns >= min_turns` — and `_advance_metadata_to_phase` (`service.py:1106`) resets pending to `False`.

Since `_DEFAULT_PHASE_MIN_TURNS` is `1` for every phase (`service.py:50-56`) and the learner turn counter is incremented earlier in the same request (`service.py:486-499`), `current_turns >= min_turns` is **always** satisfied on any turn that could have produced evidence. So the only surviving path to `pending = true` is unreachable.

Consequences:
- `workspace_modules_page.dart:872-883` `_canAdvancePhase()` returns `phaseTransitionPending` → the footer's primary FilledButton (`workspace_modules_page.dart:3373-3384`) is greyed out forever.
- `_PhaseStepperBar` / `_WorkspaceCompactHeaderStatus` are passed `phaseTransitionPending` (`workspace_modules_page.dart:1030-1036`, `1101-1104`) and never render their "ready to advance" state.
- `POST /workspaces/{id}/advance-phase` is effectively unreachable from the app; when it *is* called it raises `ValueError` → **409** (`workspaces.py:107-108`), which the UI surfaces as a raw error string.

**Decide and commit to one model:** either (a) auto-advance is the design → delete the advance-phase button and endpoint from the client contract, or (b) learner-confirmed advance is the design → remove the auto-advance block in `service.py:507-513` and let `pending` persist.

### GAP-02 (P0) — the tutor message the learner reads belongs to the *previous* phase

The tutor reply is generated for `current_phase` (`service.py:383-390`), and only *after* that does the same request advance the workspace to the next phase (`service.py:507-513`). The learner therefore sees an Engage-style hook while the header already says "Explore", and the next turn jumps straight into an Explore prompt with no bridging sentence. Repeated across five phases this is the single biggest source of "the chat feels incoherent".

Fix: either emit a synthetic phase-transition tutor message when `auto_advanced_next_phase is not None`, or defer the advance so the *next* request is the one that opens the new phase.

### GAP-03 (P0) — remediation loops: evidence is never cleared when a phase is re-entered

`_remediate_metadata_to_phase` (`service.py:1111-1120`) sends the learner back to `explore` (on `misconception`) or `elaborate` (on `partial`) but leaves `metadata["phase_evidence"]` fully intact. `_phase_is_ready` (`service.py:1064-1083`) reads that stale evidence, so the very next turn re-satisfies the requirement and the learner is auto-advanced straight back to `evaluate` — where they fail again. Ping-pong loop, no learning, no exit.

Fix: clear (or version-stamp) `phase_evidence[phase]` for every phase from the remediation target forward.

### GAP-04 (P1) — `evaluate` → posttest requires three independent evidence tags *plus* a perfect final turn

`_PHASE_REQUIRED_EVIDENCE["evaluate"]` (`service.py:78-82`) requires `independent_attempt` **and** `error_analysis` **and** `reflection` as three separate groups, and `service.py:514-521` additionally requires `evaluation_outcome == "passed"` **and** `correctness == "correct"` on the same turn. Any other outcome falls into the `else` branch (`service.py:535-536`) which resets `posttest_eligible = False`.

In practice `posttest_eligible` almost never flips true, so `showStartPosttestButton` (`workspace_modules_page.dart:915-917`) never appears and the module has no completion path. This is the terminal dead end of the feature.

### GAP-05 (P0) — when the AI provider is down, the learner is permanently stuck in Engage

`_fallback_response` (`tutor.py:507-521`) returns `evidence_tags=[]` and `confidence=0.0`. `_record_phase_evidence` (`service.py:1044-1045`) discards anything with no tags or confidence < 0.55, so **no** evidence is ever recorded during an outage and `_phase_is_ready` never returns true. There is no timeout budget, no retry, and no user-facing signal that the tutor is degraded — the chat just answers with canned text forever while the phase bar never moves.

Same applies to `_greeting_response` (`tutor.py:292-313`), which is fine as a one-off but shares the no-evidence property.

### GAP-06 (P2) — `hint_level` scaffold contract is stated to the model but never supplied

`_SYSTEM_INSTRUCTION` (`tutor.py:96-97`) tells the model "a worked example is allowed only when the **backend scaffold level** is 3 or higher", but the prompt only ships `hint_level` inside the `learning_context` JSON blob (`tutor.py:287`) — the term "scaffold level" appears nowhere in the payload. The escalation ladder in `_record_phase_evidence` (`service.py:1030-1042`) is computed but never actually gates model behaviour.

### GAP-07 (P2) — hint-level decay is asymmetric and can strand a learner at a high hint level

`service.py:1037` jumps `hint_level` to `max(failures, 3)` on the third consecutive failure, but `service.py:1040-1042` decays it by only `1` per correct answer. Combined with GAP-06 nothing observable depends on it today, but it will misbehave the moment it does.

---

## 2. Capabilities the backend has that mobile cannot reach

### GAP-08 (P0) — the canvas drawing is never actually sent; there is no image upload endpoint anywhere

This is the largest functional hole in the feature.

- Backend accepts `image_asset_id` on every workspace event (`schemas.py:23`, `workspaces.py:81`) and threads it into the input-event ledger (`inputs/service.py:22-42`), which computes `has_image` for downstream scoring.
- Mobile's `appendEvent` signature (`workspace_repository.dart:39-44`, `api_workspace_repository.dart:120-142`) has **no image parameter** and never sends the field.
- `_handleCanvasSentToChat` (`workspace_modules_page.dart:682-699`) ships only *metadata about* the drawing (`element_count`, `canvas_width`, …) — the pixels are dropped on the floor.
- **There is no image-upload endpoint on the backend at all.** `grep` across `app/api/v1/` finds `image_asset_id` referenced only as an inbound UUID; nothing ever mints one. The field is dead end-to-end.

So: the tutor is asked to teach STEAM reasoning, the learner draws a diagram, and the tutor receives the literal string `""` plus a element count. `tutor.py:442-446` then replies "I saved your canvas snapshot" — a claim that is not true in any meaningful sense.

Needs: an asset-upload endpoint, an `imageAssetId` parameter on the mobile repository, and a multimodal path in `tutor.generate_tutor_response` (which today takes no image input whatsoever).

### GAP-09 (P1) — the canvas bubble disappears from the chat one frame after it is sent

`_handleCanvasSentToChat` optimistically appends a `_WorkspaceChatEntry.canvas(snapshot)` (`workspace_modules_page.dart:685`), then `_appendWorkspaceEvent` clears and rebuilds `_chatEntries` from the server response (`workspace_modules_page.dart:788-790`). `_entriesFromEvents` (`workspace_modules_page.dart:811-817`) turns `canvas_sent` into a plain text label. Net effect: the drawing flashes on screen and is replaced by "Canvas snapshot sent (7 elements)". Reopening the session never shows it again.

### GAP-10 (P1) — `tutor_response` and `mastery_update` from the append response are parsed and then thrown away

`appendResultFromJson` (`api_workspace_repository.dart:333-384`) fully decodes `WorkspaceTutorResponse` (correctness, misconceptionStatus, confidence, evaluationOutcome, scaffoldLevel, evidenceTags) and `WorkspaceMasteryUpdate` (masteryScore, delta, evidenceCount, status). `_appendWorkspaceEvent` (`workspace_modules_page.dart:785-792`) uses **only** `result.workspace` and discards both.

So the learner never sees: whether their answer was judged correct, that a misconception was detected, that their mastery score moved, or what the current scaffold level is. All of that signal is computed, persisted, serialized, transmitted, decoded — and dropped.

### GAP-11 (P1) — `hintLevel`, `phaseEvidence`, `learningContext` are decoded but unrendered

`grep` for these across the whole 3,850-line page returns exactly three hits (`workspace_modules_page.dart:337, 604, 916`), and the only `learningContext` use is `currentModuleConceptId` passed to video generation. The remediation narrative the backend builds so carefully in `_apply_workspace_context` (`service.py:1299-1335`) — original target, diagnosis reason, route, `returns_to_original_target`, `already_understood` — is invisible to the learner. They are never told *why* they were routed to a prerequisite module.

### GAP-12 (P2) — event types the backend supports that mobile never sends

Mobile emits only `text` and `canvas_sent`. Unused: `quiz_answer` (which `tutor.py:269` explicitly handles and `mastery.py` scores differently), `media_viewed` (the only signal that the learner actually watched a generated video), and `note`. `media_viewed` in particular means video engagement is unmeasurable.

### GAP-13 (P2) — `WorkspaceEvent` model drops four fields the backend sends

`workspaceEventFromJson` (`api_workspace_repository.dart:320-331`) ignores `image_asset_id`, `media_artifact_id`, `input_event_id`, and `created_at`. Consequences: no inline media in the transcript, no timestamps, no date grouping in the chat, and no way to correlate a chat bubble with its ledger entry for debugging.

Also unused: `WorkspaceRead.last_image_asset_id` (`schemas.py:82`) has no counterpart in `WorkspaceSession` (`workspace_models.dart:33-71`).

### GAP-14 (P2) — `media_generated` events are invisible in the transcript

`_entriesFromEvents` drops any event with an empty `textPayload` (`workspace_modules_page.dart:818-820`). `queue_workspace_video_generation` writes its event with `text_payload=""` (`service.py:654`). So "a video was requested here" leaves no trace in the scrollback — the video only exists in transient `_contentMode` widget state and vanishes on session switch.

---

## 3. Error paths and lifecycle

### GAP-15 (P0) — opening a locked module returns HTTP 500

`create_or_resume_workspace` raises `ValueError("Locked modules cannot be opened before prerequisites pass.")` (`service.py:102-103`), but `POST /workspaces` catches **only** `LookupError` (`workspaces.py:50-51`). FastAPI turns the uncaught `ValueError` into a 500. Mobile shows a generic failure with no explanation of the prerequisite.

Fix: add `except ValueError → 409` to `create_workspace`.

### GAP-16 (P0) — stale local workspace ids survive logout and lock the user out of the workspace

`WorkspaceSessionStore` persists `activeWorkspaceId` per track+module in `SharedPreferences` (`workspace_session_store.dart:140-146`). `clearAll()` (line 132) is **never called** — `grep` shows no caller outside the file, and `authController.signOut()` (`auth_controller.dart:176`) does not touch it.

After logging in as a different account, `_loadWorkspace` reads the previous user's id (`workspace_modules_page.dart:132-138`) and posts it as `workspace_session_id`. `_load_workspace` filters on `user_id` (`service.py:821-826`), returns `None`, and the service raises `LookupError` → **404** (`service.py:113-114`). The learner is hard-blocked from that module until app data is cleared, with no self-recovery path.

Two fixes needed: clear the store on sign-out, **and** make `_loadWorkspace` fall back to "start fresh" on a 404 for a locally-cached id rather than surfacing the error.

### GAP-17 (P1) — double-tap send can 500 on a unique-constraint violation

The send button has `onPressed: onSend` with no guard (`workspace_modules_page.dart:3495-3499`) and `_sendMessage` (`workspace_modules_page.dart:701-717`) does not check `_isAppendingEvent`. Two in-flight appends both call `_next_event_index` (`service.py:829-835`), both get `N+1`, and the second commit violates `uq_workspace_events_session_index` (`models.py:67-70`) → unhandled `IntegrityError` → 500, and the learner's message is lost.

Fix: disable the send affordance while `_isAppendingEvent`, and make the backend index allocation collision-safe (retry, or a DB sequence per session).

### GAP-18 (P1) — resuming a finished workspace silently reopens it as `active`

`create_or_resume_workspace` picks the most recently updated session regardless of status (`service.py:119-128`) and unconditionally sets `workspace.status = "active"` (`service.py:163`). A workspace that the posttest marked `completed` (`posttests/service.py:765`) is therefore reverted to active on the next open, and `moduleCompleted` in `WorkspaceCompletionResult` (`workspace_modules_page.dart:675`) is read *before* the posttest runs, so it is effectively always `false`.

Fix: exclude `completed` sessions from auto-resume (or surface them read-only), and stop resetting status on resume.

### GAP-19 (P2) — the posttest is started twice

`POST /workspaces/{id}/start-posttest` already creates the session and returns `posttest_session_id` in the trigger (`service.py:305-331`). Mobile ignores that id, pops the route (`workspace_modules_page.dart:665-680`), and `_openWorkspaceModules` → `_openPosttest` calls `homeRepository.startPosttest(workspaceSessionId: ...)` (`app_home_page.dart:710-760`), hitting the posttest service a second time. It is idempotent thanks to `_active_posttest_for_goal` (`posttests/service.py:98-104`), so this is wasted latency rather than corruption — but the workspace endpoint's return value is pure dead weight today.

Related: `posttest_trigger.status` stays `"ready"` forever, so `_workspace_allows_posttest` (`posttests/service.py:558-563`) keeps returning true indefinitely — the eligibility gate only ever fires once.

### GAP-20 (P2) — `posttest_trigger` fields mobile parses that the backend never sends

`_workspacePosttestTriggerFromJson` (`api_workspace_repository.dart:443-458`) reads `concept_code`, `concept_title`, and `error`. `start_posttest` (`service.py:321-331`) writes none of them — it writes `workspace_session_id` and `triggered_at`, which mobile ignores. The result: `_workspaceError = updated.posttestTrigger?.error ?? posttestUnavailableMessage` (`workspace_modules_page.dart:649-651`) can only ever show the generic fallback string.

### GAP-21 (P2) — video polling has no resume and gives up silently at 5 minutes

`_pollVideoStatus` (`workspace_modules_page.dart:367-434`) is pure widget state. Backgrounding the app, switching sessions (`_resetCurrentChatState` sets `_stopVideoPolling = true`), or the 5-minute timeout all abandon the job with no way to re-attach — even though the artifact does eventually land and `_sync_ready_media_followups` (`service.py:1417-1478`) will post a follow-up on the next GET. The learner sees "generation timed out" for a video that succeeded.

### GAP-22 (P2) — `GET /workspaces` cannot list sessions without a module, and has no pagination or delete

`list_workspaces` requires both `track_id` and `module_id` as mandatory query params (`workspaces.py:18-19`). There is no cross-module "all my sessions" view, no `limit`/`offset` (`service.py:214-229` returns every session with all events eagerly loaded via `selectinload`), and no delete or rename endpoint — so the history sheet (`workspace_modules_page.dart:282-295`) grows unboundedly and the learner can never prune it. `_workspace_session_summary` also hardcodes the Indonesian string `"Belum ada pesan."` (`service.py:755`) regardless of the learner's language.

### GAP-23 (P2) — mobile's local session store duplicates a server-authoritative list

`WorkspaceSessionHistory.workspaceIds` in `SharedPreferences` mirrors what `GET /workspaces` already returns authoritatively. `sessionHistory()` (`api_workspace_repository.dart:52-65`) is used only to seed `activeWorkspaceId`, and it is the direct cause of GAP-16. The list half of this store has no reader and should be deleted; the "which session was I last in" half belongs in the API (an `is_active` flag on the summary).

---

## 4. Test coverage gaps

Backend workspace tests (`tests/api/test_workspace_api.py`, `tests/services/test_workspace_orchestration.py`) cover 8 cases, all of them about the evidence contract and anti-cheat. Nothing covers:

- auto-advance behaviour or the `phase_transition_pending` lifecycle (GAP-01/02)
- remediation re-entry (GAP-03)
- reaching `posttest_eligible` end-to-end (GAP-04)
- the AI-outage fallback path (GAP-05)
- locked-module rejection (GAP-15)
- concurrent event append (GAP-17)
- resume-after-completion (GAP-18)

Mobile has 3 tests, all in `test/api_workspace_repository_test.dart` — serialization only. There is no widget test for `WorkspaceModulesPage` at all, which is where the majority of the P0/P1 findings live.

---

## 5. Suggested order of work

| # | Item | Gaps |
|---|---|---|
| 1 | Pick one phase-advance model (auto **or** learner-confirmed) and make backend + UI agree | GAP-01, 02 |
| 2 | Clear phase evidence on remediation; relax the `evaluate` gate so a completion path exists | GAP-03, 04 |
| 3 | Make AI-outage a visible, recoverable state instead of a silent freeze | GAP-05 |
| 4 | Fix the three learner-blocking error paths: locked module 500, stale-id 404 lockout, double-send 500 | GAP-15, 16, 17 |
| 5 | Build the image-asset upload endpoint + multimodal tutor input; wire the canvas to it | GAP-08, 09 |
| 6 | Surface the feedback the backend already sends: correctness, mastery delta, hint level, learning context | GAP-10, 11 |
| 7 | Session lifecycle: stop resurrecting completed workspaces, stop double-starting the posttest | GAP-18, 19 |
| 8 | Everything else | GAP-06, 07, 12–14, 20–23 |

Items 1–3 are what make the feature feel broken to a learner today; the rest is what makes it feel thin.
