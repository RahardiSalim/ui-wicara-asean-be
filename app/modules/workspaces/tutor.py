from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import json
import logging
import os
import re
from typing import Any, NamedTuple

from app.modules.ai import ai_client
from app.modules.ai.errors import AIError, AIProviderError
from app.modules.ai.schemas import AIGenerationResponse
from app.core.language import language_display_name, normalize_language_code
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead, WorkspaceToolSuggestionRead

logger = logging.getLogger(__name__)

class TutorImageInput(NamedTuple):
    """A learner-supplied image (canvas snapshot / photo) to show the tutor."""

    file_path: str
    mime_type: str


PROMPT_VERSION = "wicara_5e_natural_progression_v9"
PHASE_SEQUENCE = ("engage", "explore", "explain", "elaborate", "evaluate")
DEFAULT_TUTOR_TIMEOUT_SECONDS = 240.0
MAX_SCAFFOLD_LEVEL = 6
WORKED_EXAMPLE_SCAFFOLD_LEVEL = 3
_TUTOR_MAX_ATTEMPTS = 2
_TUTOR_RETRY_BACKOFF_SECONDS = 0.5

_ALLOWED_EVIDENCE_TAGS = {
    "challenge_accepted",
    "prior_knowledge_shared",
    "exploration_attempt",
    "pattern_identified",
    "misconception_shifted",
    "learner_explanation",
    "micro_check_correct",
    "transfer_attempt",
    "transfer_correct",
    "independent_attempt",
    "error_analysis",
    "reflection",
}
_ALLOWED_CORRECTNESS = {"correct", "partial", "incorrect", "unknown"}
_ALLOWED_MISCONCEPTION = {"none", "suspected", "active", "resolved"}
_ALLOWED_EVALUATION_OUTCOMES = {"passed", "partial", "misconception", "continue"}

_PHASE_TRANSITION_CRITERIA: dict[str, str] = {
    "engage": (
        "Learner has explicitly accepted the investigation and shared relevant prior "
        "knowledge or an initial idea about the diagnosed learning gap."
    ),
    "explore": (
        "Learner has attempted exploration/discovery and shared observations, "
        "so they are ready for explicit explanation."
    ),
    "explain": (
        "Learner has explained the key concept and then correctly answered a separate "
        "micro-check in a later turn, so they are ready for application."
    ),
    "elaborate": (
        "Learner can apply the concept to a new/contextualized case with reasonable reasoning, "
        "so they are ready for evaluation."
    ),
    "evaluate": (
        "Final stage. Keep evaluating understanding and giving feedback."
    ),
}

_PHASE_EVIDENCE_GUIDANCE: dict[str, str] = {
    "engage": (
        "Use challenge_accepted when the learner explicitly accepts or commits to the "
        "investigation. Use prior_knowledge_shared only when they state relevant prior "
        "knowledge or an initial idea; readiness alone is not prior knowledge."
    ),
    "explore": (
        "Use exploration_attempt when the learner tries a calculation, comparison, example, "
        "or experiment. Use pattern_identified when their latest message states a correct "
        "relationship or pattern supported by that exploration. Both tags may be returned "
        "on the same turn when both are demonstrated."
    ),
    "explain": (
        "Use learner_explanation when the learner explains the key idea in their own words. "
        "Use micro_check_correct only for a correct answer to a separate micro-check in a "
        "later turn."
    ),
    "elaborate": (
        "Use transfer_attempt for a substantive attempt on a new application. Add "
        "transfer_correct when that application is correct."
    ),
    "evaluate": (
        "Use independent_attempt, error_analysis, and reflection only when each is explicitly "
        "present in the learner's final-stage response."
    ),
}

_TUTOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "next_phase_ready": {"type": "boolean"},
        "phase_reasoning": {"type": "string"},
        "phase_checkpoint_question": {"type": ["string", "null"]},
        "next_phase_opening_prompt": {"type": ["string", "null"]},
        "evidence_tags": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(_ALLOWED_EVIDENCE_TAGS)},
        },
        "correctness": {"type": "string", "enum": sorted(_ALLOWED_CORRECTNESS)},
        "misconception_status": {
            "type": "string",
            "enum": sorted(_ALLOWED_MISCONCEPTION),
        },
        "confidence": {"type": "number"},
        "evaluation_outcome": {
            "type": ["string", "null"],
            "enum": ["passed", "partial", "misconception", "continue", None],
        },
        "evidence_request": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string"},
                        "prompt": {"type": "string"},
                        "expected_evidence": {"type": "string"},
                    },
                    "required": ["type", "prompt", "expected_evidence"],
                },
                {"type": "null"},
            ]
        },
        "explanation_card": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "example": {"type": "string"},
                    },
                    "required": ["title", "summary", "example"],
                },
                {"type": "null"},
            ]
        },
        "tool_suggestion": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "enum": ["visualization"]},
                        "reason": {
                            "type": "string",
                            "enum": [
                                "learner_stuck",
                                "repeated_misconception",
                                "learner_requested",
                            ],
                        },
                        "prompt": {"type": "string"},
                    },
                    "required": ["tool", "reason", "prompt"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    },
    "required": [
        "text",
        "next_phase_ready",
        "phase_reasoning",
        "phase_checkpoint_question",
        "next_phase_opening_prompt",
        "evidence_tags",
        "correctness",
        "misconception_status",
        "confidence",
        "evaluation_outcome",
        "evidence_request",
        "explanation_card",
        "tool_suggestion",
    ],
}

_SYSTEM_INSTRUCTION = """
You are Wicara, a Socratic AI tutor for a STEAM learning platform.
Guide students using the 5E learning model: Engage, Explore, Explain, Elaborate, Evaluate.

Language rule:
- Follow the required response language exactly.
- If required response language is English, write in English.
- If required response language is Indonesian, write in Indonesian.

Teaching rules:
- Be concise: avoid long generic monologues.
- Use 1-3 short sentences and end with at most one guiding question or clear next action.
- Lead the student to discover the answer. Obey the scaffold policy supplied with each
  turn: it states the current backend scaffold level and what you may reveal at it.
- Be warm, encouraging, and precise.
- Treat a learner hypothesis, guess, or request for a simpler example as tentative. Do
  not call it understanding, mastery, or a correctly identified rule until the learner
  has supported it with reasoning or a successful application.
- Ground feedback in the latest learner action. Name only what changed or was
  demonstrated in that message. Do not open with generic praise such as "Excellent!",
  "You've correctly identified...", or "You've shown a solid understanding...".
- Preserve demonstrated progress. If earlier evidence shows that one skill is already
  working, keep that skill stable in the next task and isolate the remaining error. Do
  not reintroduce a resolved misconception unless the latest learner work actually
  demonstrates it again.
- If the learner repeats the same conceptual confusion, change teaching strategy instead
  of paraphrasing the same question: move from diagnosis to a concrete small-change,
  input-output, comparison, or visual model. For a calculation error, preserve the
  correct structure and isolate only the uncertain calculation. For terminology
  confusion, give one short definition plus an example.
- Avoid repeating the same opening pattern (for example repeated "Imagine..." hooks).
- Treat the supplied learning context as authoritative. Ground the activity in the
  diagnosed evidence and remember the learner's original target.
- Keep every task inside its current 5E phase. Never display a task for the next phase
  before the learner confirms the transition.
- Report evidence only when the latest learner message actually demonstrates it.
- Never claim mastery merely because the learner says they understand or watches media.
- When an image accompanies the turn it is the learner's own drawing or worked solution:
  read it, refer to something concrete you can actually see in it, and judge correctness
  from the work shown. Never claim to have seen a drawing when no image was supplied.
- A visualization is an optional Explore scaffold, never a phase requirement.
- Suggest a visualization only in Explore after the learner has attempted the task and is
  still confused, has repeated a misconception, or explicitly asks for a visual.
- Do not suggest a visualization merely because the tool exists.
""".strip()

_PROMPTS: dict[str, str] = {
    "engage": (
        "Topic: {topic}\n"
        "Stage: Engage\n"
        "Conversation so far:\n{history}\n\n"
        "Student latest message: {message}\n\n"
        "Respond in {response_language} with 1-2 short sentences.\n"
        "If this is the first engage turn, use one natural sentence explaining that the "
        "current prerequisite will later support the original target. Do not claim the "
        "learner said they wanted that target, and do not invent a mistake absent from the "
        "diagnosis. Mention the original target only on that first turn.\n"
        "If this is not the first engage turn, do NOT mention the original target again or "
        "start a new generic scenario; respond directly to the student's message.\n"
        "Until the learner has shared prior knowledge, end with one focused question that "
        "bridges the hook directly to a concrete example of the current topic.\n"
        "Do NOT explain the full concept yet."
    ),
    "explore": (
        "Topic: {topic}\n"
        "Stage: Explore\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "Assess the learner's response to the current Explore task. While Explore is not "
        "complete, give one probing challenge or mini experiment in {response_language} "
        "that pushes discovery. If the learner is unsure why two effects combine, make the "
        "experiment concrete: choose a small input change, track how it changes at each "
        "layer, compare the scale factors, and then ask the learner to reapply the observed "
        "pattern to the original task. Do not jump to another analogous example when the "
        "missing issue is the causal link itself. Do not label a pattern as identified until "
        "the learner states or uses it. Do not call an Explore activity transfer. When Explore is "
        "complete, give feedback only; put the Explain opening in "
        "next_phase_opening_prompt. Keep it 1-2 sentences."
    ),
    "explain": (
        "Topic: {topic}\n"
        "Stage: Explain\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "First elicit the learner's explanation in their own words only after the history "
        "shows they successfully applied the discovered model. If they explicitly say they "
        "still cannot explain the reason, stop eliciting and teach the missing conceptual "
        "model concisely (for example, sequential changes act as consecutive scale factors), "
        "then ask one concrete application question rather than asking for the same "
        "explanation again. If the latest message "
        "itself demonstrates learner_explanation, give a concise grounded formal explanation "
        "and end with exactly one concrete micro-check for the next learner turn. Also return "
        "that task in evidence_request with type=micro_check. Do not mark micro_check_correct "
        "until the learner answers it in a later turn. If phase_evidence already contains "
        "learner_explanation and the latest learner message correctly applies the concept to "
        "the requested new example, return micro_check_correct and next_phase_ready=true. "
        "When ready, give feedback only and put the Elaborate application in "
        "next_phase_opening_prompt."
    ),
    "elaborate": (
        "Topic: {topic}\n"
        "Stage: Elaborate\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "First inspect the latest student message. If it is a substantive solution to the "
        "previous application task, assess that exact solution; do not replace it with another "
        "task. Preserve any method the learner already demonstrated. If the method structure "
        "is right but one calculation is wrong, say that the structure remains right and ask "
        "the learner to recompute only that step; do not claim the earlier concept was forgotten. "
        "For a correct solution return both transfer_attempt and transfer_correct and set "
        "next_phase_ready=true. If it is an incorrect attempt, return transfer_attempt and one "
        "focused hint. Only when there is no substantive solution yet, give one new application "
        "task in {response_language}. Call it an application task, never a micro-check. When "
        "ready, give feedback only and put the independent Evaluate task in "
        "next_phase_opening_prompt."
    ),
    "evaluate": (
        "Topic: {topic}\n"
        "Stage: Evaluate\n"
        "Conversation so far:\n{history}\n\n"
        "Student answer: {message}\n\n"
        "Respond in {response_language}. Collect evaluation evidence naturally across turns. "
        "First assess an independent solution. On the next turn ask the learner to identify "
        "and correct one plausible error. Independent work plus error analysis completes the "
        "evaluation; reflection is optional and must never be requested as a required extra "
        "turn. Request only the next missing item shown by phase_evidence; "
        "do not ask the learner to recite evidence labels. If incorrect, give a hint without "
        "revealing the answer. When evaluation_outcome=passed, give concise final feedback "
        "without a question, connect the demonstrated prerequisite back to the original "
        "learning target once, and return evidence_request=null."
    ),
    "chat": (
        "Topic: {topic}\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "Respond in {response_language} as a Socratic tutor. Be concise (1-3 sentences). "
        "End with a guiding question or next action suggestion."
    ),
}

_STAGE_INTENT: dict[str, str] = {
    "engage": "spark_curiosity",
    "explore": "probe_understanding",
    "explain": "explain",
    "elaborate": "recommend_practice",
    "evaluate": "evaluate_response",
    "chat": "ask_followup",
}

_STAGE_ACTIONS: dict[str, list[str]] = {
    "engage": ["explore_topic", "ask_question", "use_canvas"],
    "explore": ["try_answer", "ask_clarification", "use_canvas"],
    "explain": ["summarize", "answer_quiz", "use_canvas"],
    "elaborate": ["apply_concept", "answer_quiz"],
    "evaluate": ["review_explanation", "retry_quiz", "continue_next_module"],
    "chat": ["ask_followup", "use_canvas", "answer_quiz"],
}


def _build_history(events: list[WorkspaceEvent], max_turns: int = 10) -> str:
    recent = events[-(max_turns * 2):]
    lines: list[str] = []
    for event in recent:
        text = event.text_payload.strip()
        if not text:
            continue
        role = "Student" if event.actor_type == "learner" else "Tutor"
        lines.append(f"{role}: {text}")
    return "\n".join(lines) if lines else "(no prior conversation)"


def _build_user_instruction(
    current_phase: str,
    topic: str,
    history: str,
    message: str,
    *,
    learner_language: str | None,
    response_language: str,
    learning_context: dict[str, Any],
) -> str:
    template = _PROMPTS.get(current_phase, _PROMPTS["chat"])
    next_phase = _next_phase(current_phase)
    scaffold_level = max(0, int(learning_context.get("scaffold_level") or 0))
    scaffold_instruction = (
        "Scaffold policy:\n"
        f"- Backend scaffold level: {scaffold_level} of {MAX_SCAFFOLD_LEVEL}.\n"
        "- Level 0-1: ask one guiding question, reveal nothing.\n"
        "- Level 2: give one targeted hint that names the idea to reconsider.\n"
        f"- Level {WORKED_EXAMPLE_SCAFFOLD_LEVEL}+: a worked example is allowed, but it must "
        "use different numbers/context than the learner's own task.\n"
        "- Never exceed what the current level permits."
    )
    transition_instruction = (
        "Phase transition check:\n"
        f"- Current phase: {current_phase}\n"
        f"- Next phase candidate: {next_phase if next_phase else '(none, final phase)'}\n"
        f"- Transition criteria: {_PHASE_TRANSITION_CRITERIA.get(current_phase, _PHASE_TRANSITION_CRITERIA['engage'])}\n"
        "- Set next_phase_ready=true only if the learner is pedagogically ready for the next phase.\n"
        "- If current phase is evaluate, always return next_phase_ready=false.\n\n"
        "Learner checkpoint:\n"
        "- When next_phase_ready=true, phase_checkpoint_question must be one concise "
        "yes/no question in the required response language.\n"
        "- Ground it in the learner's actual evidence and the specific concept, "
        "example, or task just discussed.\n"
        "- Ask the learner to confirm what they personally understood, found, "
        "explained, or applied.\n"
        "- Do not mention 5E phase names and do not use generic readiness wording "
        "such as 'Are you ready to continue?'.\n"
        "- Bad: 'Have you understood the learning goal and its challenge?'\n"
        "- Good: 'Setelah membandingkan dua interval tadi, apakah kamu sudah yakin kenapa tanda f\u2032(x) menentukan kurva naik atau turun?'\n"
        "- When next_phase_ready=false, return phase_checkpoint_question=null.\n\n"
        "Phase handoff:\n"
        "- When next_phase_ready=true, text must contain feedback about the learner's "
        "completed current-phase work only. It must not ask another learning question.\n"
        "- When next_phase_ready=true, next_phase_opening_prompt must contain exactly one "
        "concrete question or task that starts the next phase. It is stored until the learner "
        "confirms the transition, so do not repeat it in text or evidence_request.\n"
        "- The opening prompt must connect the evidence just demonstrated to the diagnosed "
        "gap and current concept.\n"
        "- For Explore, give a concrete discovery task. For Explain, ask for an own-words "
        "explanation. For Elaborate, give an application task and never label it micro-check. "
        "For Evaluate, give one independent problem without hints or its answer.\n"
        "- When next_phase_ready=false or current phase is Evaluate, return "
        "next_phase_opening_prompt=null.\n\n"
        "Evidence contract:\n"
        f"- Allowed evidence_tags: {', '.join(sorted(_ALLOWED_EVIDENCE_TAGS))}.\n"
        f"- Current-phase evidence rubric: {_PHASE_EVIDENCE_GUIDANCE.get(current_phase, '')}\n"
        "- correctness: correct|partial|incorrect|unknown.\n"
        "- misconception_status: none|suspected|active|resolved.\n"
        "- In Evaluate, evaluation_outcome is passed only with an independent attempt and "
        "error analysis; reflection is optional. Otherwise use partial, misconception, or continue.\n"
        "- In Elaborate, whenever transfer_correct is returned, also return transfer_attempt; "
        "a correct transfer necessarily includes an attempt.\n"
        "- evidence_request describes a task in the current phase only and must not claim a "
        "result. Return it as null when next_phase_ready=true or evaluation_outcome=passed.\n"
        "- explanation_card is allowed only in Explain after learner_explanation evidence.\n\n"
        "Tool suggestion contract:\n"
        "- Return tool_suggestion=null unless an optional scaffold is currently justified.\n"
        "- Only suggest {\"tool\":\"visualization\",\"reason\":\"learner_stuck|"
        "repeated_misconception|learner_requested\",\"prompt\":\"...\"}.\n"
        "- Visualization is allowed only in Explore, after an attempt plus confusion, "
        "or after an explicit learner request. It is never required for phase readiness.\n\n"
        "Output format requirement:\n"
        "Return one JSON object with keys: text, next_phase_ready, phase_reasoning, "
        "phase_checkpoint_question, next_phase_opening_prompt, "
        "evidence_tags, correctness, misconception_status, confidence, "
        "evaluation_outcome, evidence_request, explanation_card, tool_suggestion."
    )
    checkpoint_instruction = ""
    if learning_context.get("checkpoint_decision") == "stay":
        checkpoint_instruction = (
            "Checkpoint response:\n"
            "- The learner explicitly chose to stay in the current phase. This click is "
            "not learning evidence and must not be treated as an incorrect answer.\n"
            "- Set next_phase_ready=false, return no evidence_tags, and return "
            "phase_checkpoint_question=null.\n"
            "- Respond with a different scaffold that directly addresses the concept or "
            "task named by the checkpoint and conversation. Do not repeat the checkpoint, "
            "ask whether they are ready, or merely ask them to explain again."
        )
    elif learning_context.get("readiness_recheck_required"):
        checkpoint_instruction = (
            "Checkpoint recheck:\n"
            "- The learner previously chose to stay in this phase. Judge only the latest "
            "substantive response for renewed readiness.\n"
            "- Return phase evidence again only if this new response demonstrates it; do "
            "not rely on the older completed evidence by itself."
        )
    language_context = (
        f"Learner profile language: {learner_language or 'unknown'}\n"
        f"Required response language: {response_language}\n\n"
        "Language requirements:\n"
        f"- Respond only in {response_language}.\n"
        "- Do not switch language because of curriculum node title/topic metadata.\n"
        f"- If a curriculum concept name has no clean translation, keep the concept term but explain it in {response_language}.\n"
        "- Keep wording natural and concise for student chat."
    )
    return "\n\n".join(
        [
            language_context,
            scaffold_instruction,
            "Authoritative learning context:\n"
            + json.dumps(learning_context, ensure_ascii=False, default=str),
            template.format(
                topic=topic,
                history=history,
                message=message,
                learner_language=learner_language,
                response_language=response_language,
            ),
            checkpoint_instruction,
            transition_instruction,
        ]
    )


async def generate_tutor_response(
    workspace: WorkspaceSession,
    event_type: str,
    text_payload: str,
    events: list[WorkspaceEvent],
    current_phase: str,
    learner_language: str | None = None,
    image_input: TutorImageInput | None = None,
    learner_event_metadata: dict[str, Any] | None = None,
) -> tuple[TutorResponseRead | None, dict[str, Any]]:
    """
    Call the configured AI provider to generate a tutor response.
    Returns (TutorResponseRead | None, audit_metadata).
    Falls back to deterministic response if AI generation fails.
    Only returns a response for event types that warrant one.
    """
    if event_type not in {"text", "quiz_answer", "canvas_sent", "media_viewed"}:
        return None, {"ai_source": "skipped", "reason": f"no_response_for_{event_type}"}

    topic = workspace.current_topic or "this module"
    history = _build_history(events)
    phase = _normalize_phase(current_phase)
    language_code, response_language, language_source = _resolve_response_language(
        learner_language=learner_language,
        latest_message=text_payload,
    )
    workspace_metadata = workspace.metadata_json or {}
    learning_context = _safe_prompt_learning_context(workspace_metadata)
    learning_context.update(
        {
            "current_phase": phase,
            "phase_evidence": (workspace_metadata.get("phase_evidence") or {}).get(
                phase, []
            ),
            "hint_level": int(workspace_metadata.get("hint_level") or 0),
            "scaffold_level": int(workspace_metadata.get("hint_level") or 0),
            "recent_event_count": len(events),
            "readiness_recheck_required": (
                workspace_metadata.get("phase_readiness_recheck_required") == phase
            ),
        }
    )
    safe_event_metadata = learner_event_metadata or {}
    if (
        safe_event_metadata.get("interaction_type") == "phase_checkpoint"
        and safe_event_metadata.get("checkpoint_decision") == "stay"
    ):
        learning_context["checkpoint_decision"] = "stay"

    if event_type == "text" and _is_brief_greeting(text_payload):
        return (
            TutorResponseRead(
                text=_greeting_response(language_code=language_code, topic=topic),
                intent=_STAGE_INTENT.get(phase, "ask_followup"),
                next_actions=_STAGE_ACTIONS.get(phase, ["ask_followup"]),
                next_phase_ready=False,
                phase_reasoning="brief_greeting_detected",
            ),
            {
                "prompt_version": PROMPT_VERSION,
                "phase": phase,
                "stage": phase,
                "topic": topic,
                "event_type": event_type,
                "learner_language": learner_language or language_code,
                "response_language": response_language,
                "language_code": language_code,
                "language_source": language_source,
                "history_turns": history.count("\n") + 1,
                "ai_source": "deterministic_greeting",
            },
        )

    user_instruction = _build_user_instruction(
        current_phase=phase,
        topic=topic,
        history=history,
        message=text_payload or "(no message)",
        learner_language=learner_language,
        response_language=response_language,
        learning_context=learning_context,
    )

    ai_inputs: list[dict[str, Any]] = []
    if image_input is not None:
        ai_inputs.append(
            {
                "type": "image",
                "mime_type": image_input.mime_type,
                "file_path": image_input.file_path,
            }
        )

    audit: dict[str, Any] = {
        "has_image_input": image_input is not None,
        "prompt_version": PROMPT_VERSION,
        "phase": phase,
        "stage": phase,
        "topic": topic,
        "event_type": event_type,
        "learner_language": learner_language or language_code,
        "response_language": response_language,
        "language_code": language_code,
        "language_source": language_source,
        "history_turns": history.count("\n") + 1,
        "learning_context": learning_context,
    }

    last_error: Exception | None = None
    parsed: dict[str, Any] | None = None
    ai_response: AIGenerationResponse | None = None
    generation_instruction = user_instruction
    generation_attempt = 0
    for generation_attempt in range(1, _TUTOR_MAX_ATTEMPTS + 1):
        try:
            ai_response = await asyncio.wait_for(
                ai_client.generate(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    user_instruction=generation_instruction,
                    inputs=ai_inputs,
                    params={
                        "temperature": 0.0,
                        "response_format": _tutor_response_format(),
                    },
                ),
                timeout=_tutor_timeout_seconds(),
            )
        except (AIError, TimeoutError) as exc:
            last_error = exc
            logger.warning(
                "AI tutor attempt %s/%s failed: %s",
                generation_attempt,
                _TUTOR_MAX_ATTEMPTS,
                exc,
            )
            if generation_attempt < _TUTOR_MAX_ATTEMPTS:
                await asyncio.sleep(_TUTOR_RETRY_BACKOFF_SECONDS)
            continue

        parsed = _parse_structured_tutor_output(ai_response.text)
        if _tutor_payload_is_usable(parsed):
            break
        last_error = AIProviderError("Tutor returned an unusable structured response.")
        generation_instruction = (
            user_instruction
            + "\n\nYour previous response was structurally unusable. Return only the required "
            "JSON object. The text field must contain a complete human-readable tutor "
            "message, not punctuation or another encoded JSON object."
        )

    if ai_response is None or parsed is None or not _tutor_payload_is_usable(parsed):
        logger.warning(
            "AI tutor exhausted %s attempts, using deterministic fallback: %s",
            _TUTOR_MAX_ATTEMPTS,
            last_error,
        )
        audit["ai_source"] = "deterministic_fallback"
        audit["fallback_reason"] = str(last_error)
        audit["attempts"] = generation_attempt
        audit["degraded"] = True
        return _fallback_response(
            event_type,
            language_code=language_code,
            current_phase=phase,
        ), audit

    audit.update(
        {
            "ai_source": ai_response.provider,
            "ai_provider": ai_response.provider,
            "ai_model": ai_response.model,
            "finish_reason": ai_response.finish_reason,
            "input_tokens": ai_response.usage.input_tokens if ai_response.usage else None,
            "output_tokens": ai_response.usage.output_tokens if ai_response.usage else None,
            "attempts": generation_attempt,
            "generation_attempts": generation_attempt,
        }
    )
    tutor_text = _normalize_tutor_text(parsed["text"])
    next_phase_ready = parsed["next_phase_ready"]
    phase_reasoning = parsed["phase_reasoning"]
    if not tutor_text:
        tutor_text = _fallback_text(event_type, language_code=language_code)
        next_phase_ready = False
        phase_reasoning = "fallback_due_to_empty_text"
        audit["ai_source"] = "ai_empty_fallback"
    tutor_text = _enforce_brevity(tutor_text, phase=phase)
    completes_evaluate = _evaluate_turn_completes_evidence(
        learning_context=learning_context,
        evidence_tags=parsed["evidence_tags"],
    )
    previous_tutor_text = _latest_tutor_text(events)
    if (
        not (phase == "evaluate" and completes_evaluate)
        and _is_repetitive_response(tutor_text, previous_tutor_text)
    ):
        tutor_text = _anti_repeat_response(
            language_code=language_code,
            phase=phase,
            student_message=text_payload,
            topic=topic,
        )
        audit["anti_repeat_fallback"] = True
    if (
        phase == "explain"
        and "learner_explanation" in parsed["evidence_tags"]
        and "micro_check_correct" not in parsed["evidence_tags"]
    ):
        tutor_text, parsed["evidence_request"] = _ensure_explain_micro_check(
            tutor_text=tutor_text,
            evidence_request=parsed["evidence_request"],
            language_code=language_code,
        )
    if next_phase_ready or (phase == "evaluate" and completes_evaluate):
        parsed["evidence_request"] = None
    else:
        tutor_text = _ensure_current_phase_request_visible(
            tutor_text=tutor_text,
            evidence_request=parsed["evidence_request"],
            phase=phase,
            topic=topic,
            language_code=language_code,
            learning_context=learning_context,
        )
    audit["structured_parse_ok"] = parsed["parse_ok"]
    if not parsed["parse_ok"]:
        audit["structured_parse_fallback"] = True
    tool_suggestion = _resolve_tool_suggestion(
        parsed=parsed,
        phase=phase,
        learner_message=text_payload,
        workspace_metadata=workspace_metadata,
        language_code=language_code,
    )
    next_actions = list(_STAGE_ACTIONS.get(phase, ["ask_followup"]))
    if tool_suggestion is not None and "request_visualization" not in next_actions:
        next_actions.append("request_visualization")
    return TutorResponseRead(
        text=tutor_text,
        intent=_STAGE_INTENT.get(phase, "ask_followup"),
        next_actions=next_actions,
        next_phase_ready=bool(next_phase_ready) if phase != "evaluate" else False,
        phase_reasoning=phase_reasoning,
        phase_checkpoint_question=(
            parsed["phase_checkpoint_question"] if phase != "evaluate" else None
        ),
        next_phase_opening_prompt=(
            parsed["next_phase_opening_prompt"] if phase != "evaluate" else None
        ),
        evidence_tags=parsed["evidence_tags"],
        correctness=parsed["correctness"],
        misconception_status=parsed["misconception_status"],
        confidence=parsed["confidence"],
        evaluation_outcome=parsed["evaluation_outcome"],
        evidence_request=parsed["evidence_request"],
        explanation_card=parsed["explanation_card"],
        tool_suggestion=tool_suggestion,
    ), audit

def _tutor_timeout_seconds() -> float:
    raw_value = os.getenv("WICARA_WORKSPACE_TUTOR_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_TUTOR_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return DEFAULT_TUTOR_TIMEOUT_SECONDS


def _fallback_text(event_type: str, *, language_code: str) -> str:
    if language_code == "id":
        if event_type == "text":
            return (
                "Itu pemikiran yang bagus. Coba hubungkan dengan konsep modul ini, "
                "lalu gunakan kanvas kalau diagram bisa membantu menjelaskan alasanmu."
            )
        if event_type == "canvas_sent":
            return (
                "Aku sudah menyimpan gambar kanvasmu. Sekarang tulis satu kalimat "
                "yang menjelaskan apa yang ditunjukkan oleh sketsa itu."
            )
        if event_type == "quiz_answer":
            return (
                "Aku sudah mencatat jawabanmu. Tinjau lagi konsep utamanya dan coba ulang kuis jika perlu."
            )
        return "Aku sudah mencatat itu. Lanjutkan mengeksplorasi topik ini."

    if event_type == "text":
        return (
            "That's a good thought. Try connecting it to the module concept, "
            "then use the canvas if a diagram would help clarify your reasoning."
        )
    if event_type == "canvas_sent":
        return (
            "I saved your canvas snapshot. Now write one sentence explaining "
            "what the sketch proves or shows."
        )
    if event_type == "quiz_answer":
        return (
            "I recorded your answer. Review the core concept and try the quiz again if needed."
        )
    return "I recorded that. Keep exploring the topic."


def _safe_prompt_learning_context(metadata: dict[str, Any]) -> dict[str, Any]:
    source = metadata.get("learning_context")
    source = source if isinstance(source, dict) else {}
    diagnosis = source.get("diagnosis")
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    evidence = diagnosis.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    summary = evidence.get("summary")
    summary = summary if isinstance(summary, dict) else {}

    def safe_concept(value: Any) -> dict[str, Any]:
        row = value if isinstance(value, dict) else {}
        return {
            key: row.get(key)
            for key in ("concept_code", "title", "role", "route_index", "route_length")
            if row.get(key) is not None
        }

    understood = source.get("already_understood")
    safe_understood = []
    if isinstance(understood, list):
        for item in understood[:20]:
            if not isinstance(item, dict):
                continue
            safe_understood.append(
                {
                    key: item.get(key)
                    for key in ("concept_code", "title", "status")
                    if item.get(key) is not None
                }
            )
    return {
        "original_target": safe_concept(source.get("original_target")),
        "current_module": safe_concept(source.get("current_module")),
        "diagnosis": {
            "status": evidence.get("status"),
            "confidence": evidence.get("confidence"),
            "diagnostic_signals": summary.get("diagnostic_signals", []),
            "evidence_tags": [
                str(tag)
                for tag in summary.get("evidence_tags", [])
                if isinstance(tag, str)
            ][:20],
            "suspected_prerequisite_codes": [
                str(code)
                for code in summary.get("suspected_prerequisite_codes", [])
                if isinstance(code, str)
            ][:20],
            "method_invalid_detected": bool(
                summary.get("method_invalid_detected", False)
            ),
            "misconception_detected": bool(
                summary.get("misconception_detected", False)
            ),
            "reasoning_quality": summary.get("reasoning_quality"),
        },
        "already_understood": safe_understood,
        "route": [
            str(code)
            for code in source.get("route", [])
            if isinstance(code, str)
        ][:20],
        "tools": dict(source.get("tools") or {}),
        "data_minimization": "no_identity_raw_reasoning_or_attempt_ids",
    }


def _fallback_response(
    event_type: str,
    *,
    language_code: str,
    current_phase: str,
) -> TutorResponseRead:
    stage = _normalize_phase(current_phase)

    return TutorResponseRead(
        text=_fallback_text(event_type, language_code=language_code),
        intent=_STAGE_INTENT.get(stage, "ask_followup"),
        next_actions=_STAGE_ACTIONS.get(stage, ["ask_followup"]),
        next_phase_ready=False,
        phase_reasoning=None,
    )


def _resolve_response_language(
    *,
    learner_language: str | None,
    latest_message: str,
) -> tuple[str, str, str]:
    profile_language_code = normalize_language_code(learner_language)
    detected_message_language = _detect_message_language(latest_message)
    if (
        detected_message_language is not None
        and detected_message_language != profile_language_code
    ):
        return (
            detected_message_language,
            language_display_name(detected_message_language),
            "message_override",
        )
    return (
        profile_language_code,
        language_display_name(profile_language_code),
        "learner_profile",
    )


def _detect_message_language(text: str) -> str | None:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    words = re.findall(r"[a-zA-Z]+", normalized)
    if not words:
        return None
    id_keywords = {
        "aku",
        "kamu",
        "saya",
        "dan",
        "yang",
        "untuk",
        "karena",
        "tidak",
        "gak",
        "nggak",
        "apa",
        "materi",
        "ulang",
        "soal",
        "aljabar",
    }
    en_keywords = {
        "i",
        "you",
        "the",
        "and",
        "what",
        "how",
        "why",
        "because",
        "algebra",
        "expression",
        "understand",
    }
    id_hits = sum(1 for word in words if word in id_keywords)
    en_hits = sum(1 for word in words if word in en_keywords)
    if id_hits >= 2 and id_hits >= en_hits + 1:
        return "id"
    if en_hits >= 2 and en_hits >= id_hits + 1:
        return "en"
    return None


def _is_brief_greeting(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    compact = re.sub(r"[^\w\s]", "", normalized)
    return compact in {
        "halo",
        "hallo",
        "hai",
        "hi",
        "hello",
        "pagi",
        "siang",
        "sore",
        "malam",
    }


def _greeting_response(*, language_code: str, topic: str) -> str:
    if language_code == "id":
        return f"Halo, siap belajar {topic}. Apa yang sudah kamu ketahui tentang topik ini?"
    return f"Hi, ready to learn {topic}. What do you already know about this topic?"


def _enforce_brevity(text: str, *, phase: str) -> str:
    max_sentences = {
        "engage": 2,
        "explore": 2,
        "explain": 4,
        "elaborate": 3,
        "evaluate": 3,
    }.get(phase, 3)
    stripped = str(text or "").strip()
    if not stripped:
        return stripped
    parts = re.split(r"(?<=[.!?])\s+", stripped)
    cleaned = [part.strip() for part in parts if part.strip()]
    if len(cleaned) <= max_sentences:
        return stripped
    return " ".join(cleaned[:max_sentences]).strip()


def _latest_tutor_text(events: list[WorkspaceEvent]) -> str | None:
    for event in reversed(events):
        if event.actor_type != "tutor":
            continue
        text = event.text_payload.strip()
        if text:
            return text
    return None


def _is_repetitive_response(current_text: str, previous_text: str | None) -> bool:
    if not previous_text:
        return False
    current = current_text.strip().lower()
    previous = previous_text.strip().lower()
    if not current or not previous:
        return False
    if current == previous:
        return True
    if current.startswith("imagine you're") and previous.startswith("imagine you're"):
        return True
    similarity = SequenceMatcher(a=current, b=previous).ratio()
    return similarity >= 0.86


def _anti_repeat_response(
    *,
    language_code: str,
    phase: str,
    student_message: str,
    topic: str,
) -> str:
    has_message = bool(student_message.strip())
    if language_code == "id":
        prompts = {
            "engage": f"Kita fokus pada jawabanmu tentang {topic}. Bagian mana yang paling ingin kamu uji?",
            "explore": (
                "Kita ganti cara: pilih perubahan kecil pada input, ikuti perubahan itu "
                "melewati setiap lapisan, lalu bandingkan faktor skalanya. Apa yang berubah "
                "pada lapisan pertama?"
            ),
            "explain": (
                "Jangan ulangi rumusnya dulu: satu perubahan melewati dua tahap berurutan, "
                "jadi skala tahap pertama memengaruhi input tahap kedua. Coba terapkan model "
                "dua tahap itu pada contoh yang baru dibahas."
            ),
            "elaborate": (
                "Pertahankan struktur yang sudah benar dan periksa hanya hitungan bagian "
                "dalam yang masih meragukan. Berapa hasil langkah itu setelah dihitung ulang?"
            ),
            "evaluate": (
                "Pertahankan jawabanmu dan periksa satu langkah yang paling meragukan tanpa "
                "mengganti seluruh metode. Apa koreksi spesifiknya?"
            ),
        }
        return prompts.get(
            phase,
            "Lanjutkan dari poin terakhirmu dan tambahkan satu langkah konkret."
            if has_message
            else "Tambahkan satu langkah konkret.",
        )
    prompts = {
        "engage": f"Let's focus on your answer about {topic}. Which part would you test first?",
        "explore": (
            "Let's switch methods: choose a small input change, track it through each "
            "layer, and compare the scale factors. What changes at the first layer?"
        ),
        "explain": (
            "Pause the formula: one change passes through two consecutive stages, so the "
            "first scale changes what reaches the second. Apply that two-stage model to "
            "the example we just discussed."
        ),
        "elaborate": (
            "Keep the structure that already works and recompute only the uncertain inner "
            "step. What does that step give after you check it?"
        ),
        "evaluate": (
            "Keep your solution and inspect only the step you trust least instead of "
            "replacing the whole method. What specific correction is needed?"
        ),
    }
    return prompts.get(
        phase,
        "Continue from your last point and add one concrete step."
        if has_message
        else "Add one concrete step.",
    )


def _normalize_phase(phase: str | None) -> str:
    normalized = str(phase or "").strip().lower()
    return normalized if normalized in PHASE_SEQUENCE else "engage"


def _next_phase(phase: str) -> str | None:
    normalized = _normalize_phase(phase)
    index = PHASE_SEQUENCE.index(normalized)
    if index >= len(PHASE_SEQUENCE) - 1:
        return None
    return PHASE_SEQUENCE[index + 1]


def _parse_structured_tutor_output(raw_text: str) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    text = str(raw_text or "").strip()
    if not text:
        return _unverified_tutor_payload(text="")

    payload = _parse_json_payload(text)
    if payload is None:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            payload = _parse_json_payload(match.group(0))
    if payload is None:
        return _unverified_tutor_payload(text=text)

    parsed_text = str(payload.get("text") or "").strip()
    if not parsed_text:
        parsed_text = text
    next_phase_raw = payload.get("next_phase_ready")
    next_phase_ready = _coerce_bool(next_phase_raw)
    phase_reasoning_value = payload.get("phase_reasoning")
    phase_reasoning = (
        str(phase_reasoning_value).strip() if phase_reasoning_value is not None else None
    )
    if phase_reasoning == "":
        phase_reasoning = None
    phase_checkpoint_value = payload.get("phase_checkpoint_question")
    phase_checkpoint_question = (
        str(phase_checkpoint_value).strip()
        if phase_checkpoint_value is not None
        else None
    )
    if not next_phase_ready or not phase_checkpoint_question:
        phase_checkpoint_question = None
    next_phase_opening_value = payload.get("next_phase_opening_prompt")
    next_phase_opening_prompt = (
        str(next_phase_opening_value).strip()
        if next_phase_opening_value is not None
        else None
    )
    if not next_phase_ready or not next_phase_opening_prompt:
        next_phase_opening_prompt = None
    raw_tags = payload.get("evidence_tags")
    evidence_tags = (
        [
            str(tag)
            for tag in raw_tags
            if str(tag) in _ALLOWED_EVIDENCE_TAGS
        ]
        if isinstance(raw_tags, list)
        else []
    )
    correctness = str(payload.get("correctness") or "unknown").strip().lower()
    if correctness not in _ALLOWED_CORRECTNESS:
        correctness = "unknown"
    misconception_status = str(
        payload.get("misconception_status") or "none"
    ).strip().lower()
    if misconception_status not in _ALLOWED_MISCONCEPTION:
        misconception_status = "none"
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    outcome_raw = payload.get("evaluation_outcome")
    evaluation_outcome = (
        str(outcome_raw).strip().lower() if outcome_raw is not None else None
    )
    if evaluation_outcome not in _ALLOWED_EVALUATION_OUTCOMES:
        evaluation_outcome = None
    evidence_request = payload.get("evidence_request")
    if not isinstance(evidence_request, dict):
        evidence_request = None
    explanation_card = payload.get("explanation_card")
    if not isinstance(explanation_card, dict):
        explanation_card = None
    tool_suggestion = _parse_tool_suggestion(payload.get("tool_suggestion"))
    return {
        "text": parsed_text,
        "next_phase_ready": next_phase_ready,
        "phase_reasoning": phase_reasoning,
        "phase_checkpoint_question": phase_checkpoint_question,
        "next_phase_opening_prompt": next_phase_opening_prompt,
        "evidence_tags": evidence_tags,
        "correctness": correctness,
        "misconception_status": misconception_status,
        "confidence": round(confidence, 4),
        "evaluation_outcome": evaluation_outcome,
        "evidence_request": evidence_request,
        "explanation_card": explanation_card,
        "tool_suggestion": tool_suggestion,
        "parse_ok": True,
    }


def fallback_phase_opening_prompt(
    *,
    phase: str,
    topic: str,
    learner_language: str | None,
    learning_context: dict[str, Any] | None = None,
) -> str:
    """Return a phase-local opening only for legacy handoffs without an AI prompt."""
    normalized = _normalize_phase(phase)
    language_code = normalize_language_code(learner_language)
    if language_code == "id":
        prompts = {
            "explore": f"Coba satu contoh {topic}: pisahkan bagian-bagiannya dan ceritakan pola yang kamu temukan.",
            "explain": (
                f"Dari pola yang baru kamu temukan, bagaimana kamu menjelaskan {topic} dengan kata-katamu sendiri?"
            ),
            "elaborate": (
                f"Sekarang terapkan {topic} pada satu contoh baru dan tunjukkan alasan untuk setiap langkahmu."
            ),
            "evaluate": (
                f"Kerjakan satu contoh baru tentang {topic} secara mandiri dan tuliskan langkah lengkapmu tanpa petunjuk."
            ),
        }
    else:
        prompts = {
            "explore": f"Try one {topic} example: separate its parts and describe the pattern you find.",
            "explain": (
                f"From the pattern you just found, how would you explain {topic} in your own words?"
            ),
            "elaborate": (
                f"Now apply {topic} to one new example and justify each step."
            ),
            "evaluate": (
                f"Solve one new {topic} example independently and show your complete reasoning without hints."
            ),
        }
    return prompts.get(normalized, f"What do you already know about {topic}?")


def _tutor_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "workspace_tutor_response",
            "strict": True,
            "schema": _TUTOR_OUTPUT_SCHEMA,
        },
    }


def _ensure_current_phase_request_visible(
    *,
    tutor_text: str,
    evidence_request: dict[str, Any] | None,
    phase: str,
    topic: str,
    language_code: str,
    learning_context: dict[str, Any],
) -> str:
    request = evidence_request if isinstance(evidence_request, dict) else {}
    prompt = str(request.get("prompt") or "").strip()
    if "?" in tutor_text:
        return tutor_text
    if prompt and prompt.casefold() not in tutor_text.casefold():
        return f"{tutor_text.rstrip()}\n\n{prompt}".strip()
    if prompt:
        return tutor_text
    fallback = _fallback_current_phase_request(
        phase=phase,
        topic=topic,
        language_code=language_code,
        learning_context=learning_context,
    )
    return f"{tutor_text.rstrip()}\n\n{fallback}".strip()


def _evaluate_turn_completes_evidence(
    *,
    learning_context: dict[str, Any],
    evidence_tags: list[str],
) -> bool:
    phase_evidence = learning_context.get("phase_evidence")
    records = (
        phase_evidence.get("evaluate")
        if isinstance(phase_evidence, dict)
        else phase_evidence
    )
    if not isinstance(records, list):
        return False
    recorded_tags = {
        str(tag)
        for record in records
        if isinstance(record, dict)
        for tag in record.get("tags", [])
    }
    return {"independent_attempt", "error_analysis"}.issubset(
        recorded_tags.union(evidence_tags)
    )


def _fallback_current_phase_request(
    *,
    phase: str,
    topic: str,
    language_code: str,
    learning_context: dict[str, Any],
) -> str:
    original = learning_context.get("original_target")
    original = original if isinstance(original, dict) else {}
    target = str(original.get("title") or "").strip()
    if language_code == "id":
        prompts = {
            "engage": (
                f"Sebelum kembali ke {target}, bagaimana kamu sekarang menangani satu fungsi bertingkat sederhana, dan bagian mana yang masih membuatmu ragu?"
                if target
                else f"Bagaimana kamu sekarang menangani satu contoh sederhana {topic}, dan bagian mana yang masih membuatmu ragu?"
            ),
            "explore": "Pada contoh yang baru kamu coba, langkah atau hasil mana yang bisa kamu bandingkan untuk menemukan polanya?",
            "explain": "Bagian mana dari idenya yang masih belum jelas, dan bagaimana kamu akan menjelaskan bagian itu sekarang?",
            "elaborate": "Pada contoh yang baru kamu kerjakan, langkah mana yang belum selesai dan hasil apa yang kamu dapat setelah memeriksanya?",
            "evaluate": f"Langkah apa yang masih perlu kamu periksa dalam solusi {topic} ini?",
        }
    else:
        prompts = {
            "engage": (
                f"Before returning to {target}, how would you currently handle one simple nested function, and where are you still unsure?"
                if target
                else f"How would you currently handle one simple {topic} example, and where are you still unsure?"
            ),
            "explore": "In the example you just tried, which step or result could you compare to reveal the pattern?",
            "explain": "Which part of the idea is still unclear, and how would you explain that part now?",
            "elaborate": "In the example you just attempted, which unfinished step will you check next, and what result do you get?",
            "evaluate": f"Which step in this {topic} solution still needs checking?",
        }
    return prompts.get(_normalize_phase(phase), f"What would you try next with {topic}?")


def _tutor_payload_is_usable(payload: dict[str, Any]) -> bool:
    if not payload.get("parse_ok"):
        return False
    normalized_text = _normalize_tutor_text(payload.get("text"))
    return len(re.findall(r"[A-Za-z0-9]", normalized_text)) >= 8


def _unverified_tutor_payload(*, text: str) -> dict[str, Any]:
    return {
        "text": text,
        "next_phase_ready": False,
        "phase_reasoning": None,
        "phase_checkpoint_question": None,
        "next_phase_opening_prompt": None,
        "evidence_tags": [],
        "correctness": "unknown",
        "misconception_status": "none",
        "confidence": 0.0,
        "evaluation_outcome": None,
        "evidence_request": None,
        "explanation_card": None,
        "tool_suggestion": None,
        "parse_ok": False,
    }


def _parse_tool_suggestion(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    tool = str(value.get("tool") or "").strip().lower()
    reason = str(value.get("reason") or "").strip().lower()
    prompt = str(value.get("prompt") or "").strip()
    if tool != "visualization":
        return None
    if reason not in {"learner_stuck", "repeated_misconception", "learner_requested"}:
        return None
    if not prompt:
        return None
    return {
        "tool": tool,
        "reason": reason,
        "prompt": prompt[:240],
    }


def _resolve_tool_suggestion(
    *,
    parsed: dict[str, Any],
    phase: str,
    learner_message: str,
    workspace_metadata: dict[str, Any],
    language_code: str,
) -> WorkspaceToolSuggestionRead | None:
    if phase != "explore":
        return None

    explicit_request = _learner_requests_visualization(learner_message)
    suggestion = parsed.get("tool_suggestion")
    failed = (
        parsed.get("correctness") in {"incorrect", "partial"}
        or parsed.get("misconception_status") in {"suspected", "active"}
    )
    current_tags = set(parsed.get("evidence_tags") or [])
    attempted = "exploration_attempt" in current_tags or _has_phase_evidence_tag(
        workspace_metadata,
        phase="explore",
        tag="exploration_attempt",
    )
    repeated_failure = int(workspace_metadata.get("consecutive_failures") or 0) >= 2

    if explicit_request:
        reason = "learner_requested"
        prompt = _default_visualization_prompt(language_code=language_code)
    elif isinstance(suggestion, dict) and failed and attempted:
        reason = str(suggestion["reason"])
        prompt = str(suggestion["prompt"])
    elif failed and attempted and repeated_failure:
        reason = "repeated_misconception"
        prompt = _default_visualization_prompt(language_code=language_code)
    else:
        return None

    return WorkspaceToolSuggestionRead(
        tool="visualization",
        reason=reason,
        prompt=prompt,
    )


def _learner_requests_visualization(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    phrases = (
        "visual",
        "video",
        "animasi",
        "animation",
        "show me",
        "tunjukkan",
        "lihat gambar",
        "see a graph",
    )
    return any(phrase in normalized for phrase in phrases)


def _has_phase_evidence_tag(
    metadata: dict[str, Any],
    *,
    phase: str,
    tag: str,
) -> bool:
    evidence_by_phase = metadata.get("phase_evidence")
    if not isinstance(evidence_by_phase, dict):
        return False
    records = evidence_by_phase.get(phase)
    if not isinstance(records, list):
        return False
    return any(
        isinstance(record, dict)
        and isinstance(record.get("tags"), list)
        and tag in record["tags"]
        for record in records
    )


def _default_visualization_prompt(*, language_code: str) -> str:
    if language_code == "id":
        return "Mau lihat visualisasi singkat untuk membandingkan hubungan yang sedang kamu selidiki?"
    return "Would you like a short visualization of the relationship you are investigating?"


def _parse_json_payload(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_tutor_text(value: Any) -> str:
    text = str(value or "").strip()
    nested = _parse_json_payload(text)
    if nested and len(nested) == 1:
        only_value = next(iter(nested.values()))
        if isinstance(only_value, str) and only_value.strip():
            return only_value.strip()
    if text.startswith("{"):
        malformed_wrapper = re.match(
            r'^\{\s*"[^"]{0,20}"\s*:\s*"(?P<body>[\s\S]+)$',
            text,
        )
        if malformed_wrapper:
            return malformed_wrapper.group("body").rstrip('"} ').strip()
    return text


def _ensure_explain_micro_check(
    *,
    tutor_text: str,
    evidence_request: dict[str, Any] | None,
    language_code: str,
) -> tuple[str, dict[str, Any]]:
    request = dict(evidence_request or {})
    request.setdefault("type", "micro_check")
    request_prompt = str(request.get("prompt") or "").strip()
    if not request_prompt:
        request_prompt = (
            "Terapkan ide yang baru kamu jelaskan pada satu contoh baru."
            if language_code == "id"
            else "Apply the idea you just explained to one new example."
        )
        request["prompt"] = request_prompt
    request.setdefault("expected_evidence", "correct application in a later learner turn")
    prompt = f"Micro-check: {request_prompt}"
    normalized_text = tutor_text.strip()
    first_instruction = re.split(r"[.!?]", request_prompt, maxsplit=1)[0].strip()
    request_is_visible = (
        request_prompt.casefold() in normalized_text.casefold()
        or (
            len(first_instruction) >= 12
            and first_instruction.casefold() in normalized_text.casefold()
        )
    )
    if "?" not in normalized_text and not request_is_visible:
        normalized_text = f"{normalized_text}\n\n{prompt}" if normalized_text else prompt
    return normalized_text, request


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return False
