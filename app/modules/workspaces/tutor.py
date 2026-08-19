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
from app.core.config import get_settings
from app.core.language import language_display_name, normalize_language_code
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead, WorkspaceToolSuggestionRead

logger = logging.getLogger(__name__)

class TutorImageInput(NamedTuple):
    """A learner-supplied image (canvas snapshot / photo) to show the tutor."""

    file_path: str
    mime_type: str


PROMPT_VERSION = "wicara_5e_natural_progression_v13"
PHASE_SEQUENCE = ("engage", "explore", "explain", "elaborate", "evaluate")
# The combined retry budget matches the workspace client's request cap.
# Must stay under the serverless function budget (Vercel maxDuration 300s) or
# the platform kills the request before the tutor can return its own graceful
# degraded reply.
DEFAULT_TUTOR_TIMEOUT_SECONDS = 240.0
MAX_SCAFFOLD_LEVEL = 6
WORKED_EXAMPLE_SCAFFOLD_LEVEL = 3
_TUTOR_MAX_ATTEMPTS = 2
_TUTOR_RETRY_BACKOFF_SECONDS = 0.5


def demo_script_enabled() -> bool:
    """Whether the fixed Chain Rule demo is enabled for a local presentation."""

    return bool(get_settings().workspace_demo_script_mode)


def _is_demo_chain_rule_workspace(workspace: WorkspaceSession) -> bool:
    if not demo_script_enabled():
        return False
    metadata = workspace.metadata_json if isinstance(workspace.metadata_json, dict) else {}
    if bool(metadata.get("demo_script", False)):
        return True
    context = metadata.get("learning_context")
    context = context if isinstance(context, dict) else {}
    candidates = [
        workspace.current_topic or "",
        str(metadata.get("active_node_id") or ""),
        str(context.get("active_node_id") or ""),
    ]
    return any(
        "chain rule" in candidate.casefold()
        or "chain_rule" in candidate.casefold()
        or "aturan rantai" in candidate.casefold()
        or "aturan_rantai" in candidate.casefold()
        for candidate in candidates
    )


def demo_phase_opening_prompt(
    *,
    phase: str,
    topic: str,
    learner_language: str | None,
    learning_context: dict[str, Any] | None = None,
    force_demo: bool = False,
) -> str:
    """Presentation-only phase openings for the deterministic Chain Rule path."""

    is_chain_rule = any(
        marker in topic.casefold()
        for marker in ("chain rule", "chain_rule", "aturan rantai", "aturan_rantai")
    )
    if not demo_script_enabled() or (not force_demo and not is_chain_rule):
        return fallback_phase_opening_prompt(
            phase=phase,
            topic=topic,
            learner_language=learner_language,
            learning_context=learning_context,
        )
    prompts = {
        "engage": (
            "Let’s look at one answer from your pretest.\n\n"
            "f(x) = sin(πx²)  →  f′(x) = cos(πx²)\n\n"
            "You differentiated the sine correctly, but something inside it was left unchanged. "
            "What part do you think that is?"
        ),
        "explain": (
            "Exactly. Now explain it in your own words: why can’t we stop at cos(πx²)?"
        ),
        "elaborate": (
            "Now let’s see if that idea transfers. Differentiate cos(2x³)."
        ),
        "evaluate": "The guided practice is complete. Continue to the post-test.",
    }
    prompt = prompts.get(_normalize_phase(phase))
    if prompt is not None:
        return prompt
    return fallback_phase_opening_prompt(
        phase=phase,
        topic=topic,
        learner_language=learner_language,
        learning_context=learning_context,
    )


def _demo_script_response(
    *,
    workspace: WorkspaceSession,
    phase: str,
) -> tuple[TutorResponseRead, dict[str, Any]] | None:
    """Return the next fixed demo turn; learner text intentionally is not judged."""

    if not _is_demo_chain_rule_workspace(workspace):
        return None
    metadata = workspace.metadata_json if isinstance(workspace.metadata_json, dict) else {}
    try:
        step = max(0, int(metadata.get("demo_script_step", 0)))
    except (TypeError, ValueError):
        step = 0

    scripted: dict[int, dict[str, Any]] = {
        0: {
            # The learner's first reply answers the fixed Engage opening. The
            # service advances to Explore only after returning this scripted
            # response, so labelling it Explore made the first turn miss the
            # script and invoke the live provider.
            "phase": "engage",
            "text": (
                "Exactly. Let’s see why that matters.\n\n"
                "Before I explain it, try changing x here: x → πx² → sin(πx²). "
                "Which part reacts first when x changes?"
            ),
            "tool": "interactive_function_flow",
            "prompt": "Drag x and watch the change move through πx², then sin(πx²).",
            "ready": True,
        },
        1: {
            "phase": "explore",
            "text": "Yes—the inner part πx² reacts first. And after πx² changes, what changes next?",
        },
        2: {
            "phase": "explore",
            "text": "Right. So does the change happen in one step, or does it pass through both functions?",
        },
        3: {
            "phase": "explore",
            "text": (
                "Exactly: it passes through both. You have traced the two stages of change.\n\n"
                "Now explain it in your own words: why can’t we stop at cos(πx²)?"
            ),
            "ready": True,
            "tags": ["exploration_attempt", "pattern_identified"],
        },
        4: {
            "phase": "explain",
            "text": (
                "That’s it. Because πx² is also changing, both rates of change matter.\n\n"
                "d/dx sin(πx²) = cos(πx²) · 2πx\n\n"
                "We multiply the derivative of the outside by the derivative of the inside. "
                "This is the Chain Rule.\n\n"
                "Here is a short video to help you visualize how the change moves through both functions."
            ),
            "ready": True,
            "tags": ["learner_explanation", "micro_check_correct"],
            "tool": "demo_chain_rule_video",
            "prompt": "/demo-media/ChainRuleLesson.mp4",
            "after_text": "Now let’s see if that idea transfers. Differentiate cos(2x³).",
        },
        5: {
            "phase": "elaborate",
            "text": (
                "Your Chain Rule structure is correct. Keep that. Check only one thing: "
                "what is d/dx(2x³)?"
            ),
            "tags": ["transfer_attempt"],
        },
        6: {
            "phase": "elaborate",
            "text": "Right. So your final derivative?",
            "tags": ["transfer_attempt"],
        },
        7: {
            "phase": "elaborate",
            "text": "Correct. You can now apply the Chain Rule independently. Your post-test is ready.",
            "ready": True,
            "tags": ["transfer_attempt", "transfer_correct"],
        },
    }
    turn = scripted.get(step)
    if turn is None or turn["phase"] != phase:
        return None
    tool = None
    if turn.get("tool"):
        tool = WorkspaceToolSuggestionRead(
            tool=str(turn["tool"]),
            reason=(
                "Watch the Chain Rule explanation before applying it."
                if turn["tool"] == "demo_chain_rule_video"
                else "Follow the change through the nested functions."
            ),
            prompt=str(turn["prompt"]),
            after_text=(
                str(turn["after_text"]) if turn.get("after_text") else None
            ),
        )
    response = TutorResponseRead(
        text=str(turn["text"]),
        intent=_STAGE_INTENT.get(phase, "ask_followup"),
        next_actions=_STAGE_ACTIONS.get(phase, ["ask_followup"]),
        next_phase_ready=bool(turn.get("ready", False)),
        phase_reasoning="demo_script",
        phase_checkpoint_question=turn.get("checkpoint"),
        evidence_tags=list(turn.get("tags", [])),
        correctness="correct" if turn.get("tags") else "unknown",
        misconception_status="none",
        confidence=1.0,
        tool_suggestion=tool,
    )
    return response, {
        "ai_source": "demo_script",
        "demo_script_step": step,
        "demo_script_next_step": step + 1,
        "degraded": False,
    }

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
        "Learner has completed three progressively less-scaffolded application attempts in "
        "this phase, with the concept applied correctly across the set."
    ),
    "evaluate": (
        "Final stage is the independent posttest; do not create another tutor exercise."
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
        "Use transfer_attempt for each substantive application attempt in this phase. Add "
        "transfer_correct when that application is correct. The learner must complete "
        "three progressively less-scaffolded applications before this phase is ready."
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
You are a warm, patient teacher who is glad this learner showed up. Talk to them
like a person you know, not like a grading function. Open by acknowledging the
specific thing they just tried, naming what was right about it however small, so
they can tell you actually read their work; keep that grounded in what they wrote
rather than generic praise. Let your phrasing vary the way a real teacher's does,
and when a learner is stuck make them feel accompanied rather than audited. Never
lecture, never flatter, but do not be curt.
Explain, do not just rule. When you affirm or correct something, give the one
concrete reason it holds — the step, the substitution, the quantity being
compared — so the learner walks away understanding why, not only that they were
right or wrong. That is the difference between a verdict and teaching. Earn every
sentence: elaborate on the idea, never pad with restatement, filler openers, or a
summary of what you are about to say.
Reply only in the required language, in 2–4 sentences and one action.
Context-clarity rule: every action states its referent, action, and purpose. Name the
specific expression; never say “layers”, “change”, or “pattern” without it. Define a
new symbol such as u in the same turn. A brief reply such as “x²”, “huh?”, or “okay”
is not readiness: respond to it or clarify before a multi-step task.
Never ask a question the learner has already been asked. If they did not engage with
your last question, that question did not work: pick a different route into the same
idea rather than restating it with a new preamble. Repeating yourself reads as not
listening, and the learner cannot tell you are waiting for something specific.
When the learner asks you a direct question, answer it first, in a sentence or two,
before steering back to the phase. Deflecting a genuine question into your own probe
is the fastest way to lose them. Answering it is not off-task: it is the moment they
are most ready to learn.
Treat a learner hypothesis as tentative. Ground feedback in the latest learner action;
do not open with generic praise such as "Excellent!". Preserve demonstrated progress
and isolate only the remaining error. On repeated confusion, change strategy; for a
calculation error preserve the method and isolate the arithmetic. Before affirming a
number, identify its quantity; never confuse a rate with a difference.
Stay in the supplied phase and report evidence only from the latest message. Mention
the original target only in first Engage. A visualization is optional only in Explore
after an attempt plus confusion or an explicit request. For an image, refer only to
work actually visible in it.
""".strip()

_PROMPTS: dict[str, str] = {
    "engage": (
        "Topic: {topic}\n"
        "Stage: Engage\n"
        "Conversation so far:\n{history}\n\n"
        "Student latest message: {message}\n\n"
        "Respond in {response_language} with 2-3 sentences.\n"
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
        "complete, first respond to the exact claim, question, or obstacle in the latest "
        "message. Then give one probing challenge or mini experiment in {response_language} "
        "that pushes discovery. If the learner is unsure why two effects combine, make the "
        "experiment concrete: choose a small input change, track how it changes at each "
        "named expression, and ask one fully specified numerical question the learner can "
        "answer without a calculator. Ask for only one quantity per turn. State why that "
        "quantity is being found before asking it. Do not stop after merely announcing the "
        "input change. Compare the scale factors, and then ask the learner to reapply the "
        "observed pattern to the original task. Do not jump to another analogous example "
        "unless you first say why it makes the same missing causal link simpler. Do not label "
        "a pattern as identified until "
        "the learner states or uses it. Do not call an Explore activity transfer. When Explore is "
        "complete, give feedback only; put the Explain opening in "
        "next_phase_opening_prompt. Keep it 2-3 sentences."
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
        "using the exact expression currently being discussed. Then ask one concrete application question rather than asking for the same "
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
        "This phase uses a short guided practice ladder. The authoritative learning context "
        "contains elaborate_application_count and elaborate_success_count.\n"
        "Start with a supported application, then reduce the hint on each new problem. "
        "Keep giving a new problem until three applications have been completed correctly. "
        "Do not call any of these tasks a micro-check.\n"
        "First inspect the latest student message. If it is a substantive solution to the "
        "previous application task, assess that exact solution; do not replace it with another "
        "task. Preserve any method the learner already demonstrated. If the method structure "
        "is right but one calculation is wrong, say that the structure remains right and ask "
        "the learner to recompute only that step; do not claim the earlier concept was forgotten. "
        "For a correct solution return both transfer_attempt and transfer_correct. Set "
        "next_phase_ready=true only after the context shows three correct applications. "
        "If it is an incorrect attempt, return transfer_attempt and one focused hint. Only "
        "when there is no substantive solution yet, give one new guided application task in "
        "{response_language}. Name the exact expression, the one step to perform, and which "
        "already-demonstrated skill the new example keeps stable or which new skill it isolates. "
        "Keep it strictly within the current topic; do not add analysis "
        "or skills from the original target. When the third application is correct, give "
        "feedback only and leave next_phase_opening_prompt null; the next step is the posttest."
    ),
    "evaluate": (
        "Topic: {topic}\n"
        "Stage: Evaluate\n"
        "Conversation so far:\n{history}\n\n"
        "Student answer: {message}\n\n"
        "The guided workspace is complete: the posttest is the Evaluate assessment. Do not "
        "create a new exercise, ask for evidence, or give a remediation task here. Briefly "
        "direct the learner to begin the posttest and return evidence_request=null."
    ),
    "chat": (
        "Topic: {topic}\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "Respond in {response_language} as a Socratic tutor. Be concise (2-4 sentences). "
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
        "concrete question or task that starts the next phase, except when the current phase "
        "is Elaborate and its three guided applications are complete; in that case leave it "
        "null because the posttest starts next. The prompt is stored until the learner "
        "confirms the transition, so do not repeat it in text or evidence_request.\n"
        "- The opening prompt must connect the evidence just demonstrated to the current "
        "concept only. Never mention or test the original target in a phase opening.\n"
        "- For Explore, give a concrete discovery task. For Explain, ask for an own-words "
        "explanation. For Elaborate, give the next guided application and never label it "
        "micro-check; after three correct applications, give feedback only and leave the "
        "opening null because the posttest is next. For legacy Evaluate, give one independent "
        "problem without hints or its answer.\n"
        "- When next_phase_ready=false or current phase is Evaluate, return "
        "next_phase_opening_prompt=null.\n\n"
        "Evidence contract:\n"
        f"- Allowed evidence_tags: {', '.join(sorted(_ALLOWED_EVIDENCE_TAGS))}.\n"
        f"- Current-phase evidence rubric: {_PHASE_EVIDENCE_GUIDANCE.get(current_phase, '')}\n"
        "- correctness: correct|partial|incorrect|unknown.\n"
        "- misconception_status: none|suspected|active|resolved.\n"
        "- In legacy Evaluate, evaluation_outcome is passed only with an independent attempt "
        "and error analysis; reflection is optional. Otherwise use partial, misconception, "
        "or continue.\n"
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
    phase_evidence = learning_context.get("phase_evidence")
    active_context: dict[str, Any] = {
        "scaffold_level": scaffold_level,
        "phase_evidence": phase_evidence[-6:] if isinstance(phase_evidence, list) else [],
    }
    if current_phase == "engage":
        active_context["diagnosis"] = learning_context.get("diagnosis", {})
        active_context["original_target"] = learning_context.get("original_target", {})
    if current_phase == "elaborate":
        active_context["applications"] = learning_context.get("elaborate_application_count", 0)
        active_context["correct_applications"] = learning_context.get(
            "elaborate_success_count", 0
        )
    active_transition = (
        f"Phase {current_phase}; next {next_phase or 'none'}. Ready only when: "
        f"{_PHASE_TRANSITION_CRITERIA.get(current_phase, _PHASE_TRANSITION_CRITERIA['engage'])}. "
        f"Evidence: {_PHASE_EVIDENCE_GUIDANCE.get(current_phase, '')}. "
        "Use latest-message evidence only. If ready, give feedback plus one specific yes/no "
        "checkpoint; otherwise checkpoint is null. Elaborate ends at posttest."
    )
    return "\n\n".join(
        [
            f"Reply only in {response_language}.",
            scaffold_instruction,
            "Context: " + json.dumps(active_context, ensure_ascii=False, default=str),
            template.format(
                topic=topic,
                history=history,
                message=message,
                learner_language=learner_language,
                response_language=response_language,
            ),
            checkpoint_instruction,
            active_transition,
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
    demo_response = _demo_script_response(workspace=workspace, phase=phase)
    if demo_response is not None:
        # A small deliberate pause makes the pre-scripted tutor feel like a real
        # turn without risking a provider timeout during a live presentation.
        await asyncio.sleep(2)
        return demo_response
    if _is_demo_chain_rule_workspace(workspace):
        # A presentation session must never silently fall back to the live
        # provider. This only occurs if an old/corrupt session has an
        # impossible script cursor; the learner can reopen the prepared demo.
        return (
            TutorResponseRead(
                text=(
                    "This prepared demo has reached an out-of-sequence step. "
                    "Please reopen Prepared Chain Rule demo from Tracks."
                ),
                intent="ask_followup",
                next_actions=["restart_demo"],
                phase_reasoning="demo_script_out_of_sequence",
                misconception_status="none",
                confidence=1.0,
            ),
            {"ai_source": "demo_script_out_of_sequence", "degraded": False},
        )
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
    if phase == "elaborate":
        elaborate_records = learning_context.get("phase_evidence")
        if not isinstance(elaborate_records, list):
            elaborate_records = []
        learning_context["elaborate_application_count"] = sum(
            1
            for record in elaborate_records
            if isinstance(record, dict)
            and "transfer_attempt" in (record.get("tags") or [])
        )
        learning_context["elaborate_success_count"] = sum(
            1
            for record in elaborate_records
            if isinstance(record, dict)
            and "transfer_correct" in (record.get("tags") or [])
        )
    safe_event_metadata = learner_event_metadata or {}
    checkpoint_stay = (
        safe_event_metadata.get("interaction_type") == "phase_checkpoint"
        and safe_event_metadata.get("checkpoint_decision") == "stay"
    )
    if checkpoint_stay:
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
    timeout_budget_seconds = _tutor_timeout_seconds()
    deadline = asyncio.get_running_loop().time() + timeout_budget_seconds
    for generation_attempt in range(1, _TUTOR_MAX_ATTEMPTS + 1):
        remaining_seconds = deadline - asyncio.get_running_loop().time()
        if remaining_seconds <= 0:
            last_error = TimeoutError(
                f"Tutor response exceeded the {timeout_budget_seconds:.0f}-second total timeout."
            )
            break
        try:
            ai_response = await asyncio.wait_for(
                ai_client.generate(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    user_instruction=generation_instruction,
                    inputs=ai_inputs,
                    params={
                        # A tutor at 0.0 answers every learner in the same flat
                        # register. Enough warmth to sound human, low enough to
                        # keep the phase discipline below.
                        "temperature": 0.6,
                        "response_format": _tutor_response_format(),
                    },
                ),
                timeout=remaining_seconds,
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
            student_message=text_payload,
            topic=topic,
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
    tutor_text = _ensure_initial_target_bridge(
        tutor_text,
        phase=phase,
        events=events,
        topic=topic,
        language_code=language_code,
        learning_context=learning_context,
    )
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
        and (
            _is_repetitive_response(tutor_text, previous_tutor_text)
            or _repeats_recent_question(tutor_text, _recent_tutor_questions(events))
        )
    ):
        tutor_text = _anti_repeat_response(
            language_code=language_code,
            phase=phase,
            student_message=text_payload,
            topic=topic,
        )
        audit["anti_repeat_fallback"] = True
    if (
        checkpoint_stay
        and phase == "explore"
        and re.search(
            r"\b(?:inner|outer|inside|outside|bagian dalam|bagian luar)\b",
            history,
            flags=re.IGNORECASE,
        )
    ):
        tutor_text = _checkpoint_stay_layer_scaffold(language_code=language_code)
        parsed["evidence_request"] = None
        parsed["phase_checkpoint_question"] = None
        parsed["next_phase_opening_prompt"] = None
        next_phase_ready = False
        audit["checkpoint_stay_strategy"] = "layer_flow_representation"
    parsed["evidence_request"] = _limit_phase_evidence_request(
        parsed["evidence_request"],
        phase=phase,
        topic=topic,
        language_code=language_code,
        learning_context=learning_context,
    )
    has_recorded_explanation = _has_phase_evidence_tag(
        workspace_metadata,
        phase="explain",
        tag="learner_explanation",
    )
    if (
        phase == "explain"
        and "micro_check_correct" in parsed["evidence_tags"]
        and not has_recorded_explanation
    ):
        parsed["evidence_tags"] = [
            tag
            for tag in parsed["evidence_tags"]
            if tag != "micro_check_correct"
        ]
        next_phase_ready = False
        parsed["phase_checkpoint_question"] = None
        parsed["next_phase_opening_prompt"] = None
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
    phase_checkpoint_question = _ground_checkpoint_question(
        parsed["phase_checkpoint_question"],
        language_code=language_code,
    )
    if next_phase_ready and phase != "evaluate" and not phase_checkpoint_question:
        phase_checkpoint_question = _fallback_phase_checkpoint_question(
            phase=phase,
            topic=topic,
            language_code=language_code,
        )
    raw_opening_prompt = parsed["next_phase_opening_prompt"]
    next_phase_opening_prompt = (
        str(raw_opening_prompt).strip()
        if raw_opening_prompt is not None
        else None
    )
    return TutorResponseRead(
        text=tutor_text,
        intent=_STAGE_INTENT.get(phase, "ask_followup"),
        next_actions=next_actions,
        next_phase_ready=bool(next_phase_ready) if phase != "evaluate" else False,
        phase_reasoning=phase_reasoning,
        phase_checkpoint_question=(
            phase_checkpoint_question if phase != "evaluate" else None
        ),
        next_phase_opening_prompt=(
            next_phase_opening_prompt if phase != "evaluate" else None
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
    student_message: str = "",
    topic: str = "this topic",
) -> TutorResponseRead:
    stage = _normalize_phase(current_phase)

    return TutorResponseRead(
        text=_anti_repeat_response(
            language_code=language_code,
            phase=stage,
            student_message=student_message,
            topic=topic,
        ),
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


def _ensure_initial_target_bridge(
    text: str,
    *,
    phase: str,
    events: list[WorkspaceEvent],
    topic: str,
    language_code: str,
    learning_context: dict[str, Any],
) -> str:
    if _normalize_phase(phase) != "engage" or any(
        event.actor_type == "tutor" for event in events
    ):
        return text
    original = learning_context.get("original_target")
    original = original if isinstance(original, dict) else {}
    target = str(original.get("title") or "").strip()
    if not target or _concept_is_mentioned(target, text):
        return text
    bridge = (
        f"{topic} ini nantinya akan mendukung {target}."
        if language_code == "id"
        else f"This work on {topic} will support {target} later."
    )
    return f"{bridge} {text}".strip()


def _concept_is_mentioned(concept: str, text: str) -> bool:
    concept_terms = [
        term for term in re.findall(r"[a-z0-9]+", concept.casefold()) if len(term) >= 4
    ]
    text_terms = re.findall(r"[a-z0-9]+", text.casefold())
    if not concept_terms:
        return concept.casefold() in text.casefold()
    matches = sum(
        1
        for concept_term in concept_terms
        if any(
            text_term.startswith(concept_term[:5])
            or concept_term.startswith(text_term[:5])
            for text_term in text_terms
            if len(text_term) >= 4
        )
    )
    return matches / len(concept_terms) >= 0.6


def _ground_checkpoint_question(
    question: str | None,
    *,
    language_code: str,
) -> str | None:
    grounded = str(question or "").strip()
    if not grounded:
        return None
    if language_code == "id":
        leading = re.match(
            r"^apakah kamu (?:sudah |merasa )?yakin bahwa\s+(.+?)\??$",
            grounded,
            flags=re.IGNORECASE,
        )
        if leading:
            statement = leading.group(1).rstrip(" ?.")
            return f"Apakah hasil terakhirmu mendukung kesimpulan bahwa {statement}?"
        grounded = re.sub(
            r",\s*apakah kamu (?:sudah |merasa )?yakin (?:bahwa|untuk)\s+",
            ", apakah bukti itu mendukung ",
            grounded,
            count=1,
            flags=re.IGNORECASE,
        )
        return grounded

    leading = re.match(
        r"^(?:are you|do you feel) (?:now )?confident that\s+(.+?)\??$",
        grounded,
        flags=re.IGNORECASE,
    )
    if leading:
        statement = re.sub(
            r"\s+and (?:that )?you can explain why$",
            "",
            leading.group(1).rstrip(" ?."),
            flags=re.IGNORECASE,
        )
        return f"Does your latest work support this conclusion: {statement}?"
    grounded = re.sub(
        r",\s*(?:are you|do you feel) (?:now )?confident (?:that|in)\s+",
        ", does that evidence support ",
        grounded,
        count=1,
        flags=re.IGNORECASE,
    )
    return grounded


def _fallback_phase_checkpoint_question(
    *,
    phase: str,
    topic: str,
    language_code: str,
) -> str:
    normalized = _normalize_phase(phase)
    if language_code == "id":
        prompts = {
            "engage": f"Apakah titik awal tentang {topic} ini sesuai dengan yang ingin kamu selidiki?",
            "explore": f"Apakah perbandingan terakhirmu mendukung pola yang kamu temukan untuk {topic}?",
            "explain": f"Apakah contoh terpisah tadi mendukung penjelasanmu sendiri tentang {topic}?",
            "elaborate": f"Apakah penerapan yang baru kamu koreksi menunjukkan cara memakai {topic} pada contoh baru?",
        }
    else:
        prompts = {
            "engage": f"Does this starting point for {topic} match what you want to investigate?",
            "explore": f"Does your latest comparison support the pattern you found for {topic}?",
            "explain": f"Did the separate example support your own explanation of {topic}?",
            "elaborate": f"Does your corrected application show how to use {topic} on a new example?",
        }
    return prompts.get(normalized, f"Does your latest work support moving forward with {topic}?")


def _enforce_brevity(text: str, *, phase: str) -> str:
    max_sentences = {
        "engage": 4,
        "explore": 4,
        "explain": 6,
        "elaborate": 5,
        "evaluate": 4,
    }.get(phase, 4)
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


_TUTOR_QUESTION_LOOKBACK = 4


def _recent_tutor_questions(events: list[WorkspaceEvent]) -> list[str]:
    """The questions the tutor has already put to this learner, newest first."""

    questions: list[str] = []
    seen_turns = 0
    for event in reversed(events):
        if event.actor_type != "tutor":
            continue
        seen_turns += 1
        if seen_turns > _TUTOR_QUESTION_LOOKBACK:
            break
        questions.extend(_question_sentences(event.text_payload))
    return questions


def _question_sentences(text: str) -> list[str]:
    return [
        sentence.strip().lower()
        for sentence in re.split(r"(?<=[.!?？])\s+", text)
        if sentence.strip().endswith(("?", "？")) and len(sentence.strip()) > 15
    ]


def _repeats_recent_question(current_text: str, asked: list[str]) -> bool:
    """Catch the same question wearing a new preamble.

    _is_repetitive_response compares whole replies against the previous one, so
    a tutor that keeps re-asking "jika x berubah dari 1 menjadi 1.1, berapa
    perubahan x^2?" under a fresh opening sentence slips past it every time.
    """

    for sentence in _question_sentences(current_text):
        for previous in asked:
            # Containment, not similarity: the repeat is usually the old
            # question with a fresh sentence bolted on the front, which drags a
            # plain ratio well below any useful threshold.
            match = SequenceMatcher(a=sentence, b=previous).find_longest_match(
                0, len(sentence), 0, len(previous)
            )
            if match.size >= 0.8 * min(len(sentence), len(previous)):
                return True
    return False


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
                "Kita ganti cara. Tulis ekspresi tepat yang sedang kita bahas, lalu pilih "
                "satu perubahan kecil pada input dan hitung perubahan pada operasi pertamanya. "
                "Ini menunjukkan nilai apa yang diteruskan ke operasi berikutnya."
            ),
            "explain": (
                "Gunakan ekspresi yang baru dibahas: tulis operasi pertama lalu operasi kedua. "
                "Perubahan dari operasi pertama menjadi input bagi operasi kedua; jelaskan "
                "bagaimana dua faktor perubahan itu digabungkan."
            ),
            "elaborate": (
                "Pertahankan struktur yang sudah benar dan periksa hanya hitungan bagian "
                "dalam yang masih meragukan. Berapa hasil langkah itu setelah dihitung ulang?"
            ),
            "evaluate": (
                "Latihan workspace sudah selesai. Lanjutkan ke posttest untuk evaluasi mandiri."
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
            "Let's switch methods. Write the exact expression we are discussing, then choose "
            "one small input change and calculate the change after its first operation. This "
            "shows what value reaches the next operation."
        ),
        "explain": (
            "Use the expression we just discussed: write its first operation and then its "
            "second operation. The first change becomes input to the second, so explain how "
            "their change factors combine."
        ),
        "elaborate": (
            "Keep the structure that already works and recompute only the uncertain inner "
            "step. What does that step give after you check it?"
        ),
        "evaluate": (
            "The guided workspace is complete. Start the posttest for the independent evaluation."
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


def _checkpoint_stay_layer_scaffold(*, language_code: str) -> str:
    if language_code == "id":
        return (
            "Kita ganti representasi: tulis alurnya sebagai input → bagian dalam → "
            "bagian luar, lalu beri label faktor perubahan pada setiap panah. Operasi apa "
            "yang menggabungkan kedua faktor itu dari input sampai output?"
        )
    return (
        "Let's switch representations: write the flow as input → inner → outer, then "
        "label each arrow with its change factor. What operation combines those two "
        "factors from input to output?"
    )


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
    if normalized == "engage":
        return _engage_opening_from_pretest(
            topic=topic,
            language_code=language_code,
            learning_context=learning_context or {},
        )
    if language_code == "id":
        prompts = {
            "explore": (
                f"Tulis satu ekspresi tepat untuk contoh {topic} yang ingin kita cek. "
                "Kita akan menamai setiap operasinya agar bisa melacak perubahan inputnya."
            ),
            "explain": (
                f"Gunakan ekspresi {topic} yang baru kamu selesaikan: sebutkan operasinya "
                "secara berurutan dan jelaskan mengapa hasil tiap operasi memengaruhi operasi berikutnya."
            ),
            "elaborate": (
                f"Pilih satu ekspresi baru untuk menerapkan {topic}. Tunjukkan satu langkah "
                "yang ingin kamu periksa, supaya kita bisa memisahkan aturan yang sudah kuat dari yang baru dilatih."
            ),
            "evaluate": (
                "Latihan workspace sudah selesai. Lanjutkan ke posttest untuk evaluasi mandiri."
            ),
        }
    else:
        prompts = {
            "explore": (
                f"Write one exact {topic} expression you want to inspect. We will name each "
                "operation so we can trace how an input change moves through it."
            ),
            "explain": (
                f"Use the exact {topic} expression you just solved: name its operations in "
                "order and explain why the output of one operation affects the next."
            ),
            "elaborate": (
                f"Choose one new expression for applying {topic}. Show one step you want to "
                "check, so we can separate the rule that is stable from the rule being practised."
            ),
            "evaluate": (
                "The guided workspace is complete. Continue to the posttest for the independent evaluation."
            ),
        }
    return prompts.get(normalized, f"What do you already know about {topic}?")


def _engage_opening_from_pretest(
    *,
    topic: str,
    language_code: str,
    learning_context: dict[str, Any],
) -> str:
    diagnosis = learning_context.get("diagnosis")
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    reason = _learner_facing_diagnosis_reason(
        str(diagnosis.get("reason") or "").strip()
    )
    original_target = learning_context.get("original_target")
    original_target = original_target if isinstance(original_target, dict) else {}
    target = str(original_target.get("title") or "").strip()
    topic_key = topic.casefold()
    is_chain_rule = "chain" in topic_key or "rantai" in topic_key

    if language_code == "id":
        diagnosis_line = reason or f"Hasil pretest menunjukkan ada langkah penting pada {topic} yang perlu kita cek."
        bridge = (
            f"Kita rapikan ini sebelum kembali ke {target}."
            if target
            else f"Kita rapikan bagian {topic} ini dulu."
        )
        question = (
            "Pada $f(x)=\\sin(x^2)$, bagian mana yang berubah lebih dulu ketika $x$ berubah?"
            if is_chain_rule
            else f"Pada satu contoh {topic}, langkah mana yang menurutmu perlu diperiksa lebih dulu?"
        )
    else:
        diagnosis_line = reason or f"Your pretest points to one important step in {topic} to check."
        bridge = (
            f"Let's repair it before returning to {target}."
            if target
            else f"Let's repair that part of {topic}."
        )
        question = (
            "In $f(x)=\\sin(x^2)$, which expression changes first when $x$ changes?"
            if is_chain_rule
            else f"In one {topic} example, which step would you check first?"
        )
    return f"{diagnosis_line} {bridge} {question}"


def _learner_facing_diagnosis_reason(reason: str) -> str:
    """Keep pretest processing telemetry out of the learner-facing opening."""
    telemetry_markers = (
        "written explanations were analyzed",
        "work images were vision-evaluated",
        "canvas submissions were stored",
        "diagnostic insight",
        "penjelasan tertulis dianalisis",
        "foto/coretan dianalisis",
    )
    if any(marker in reason.casefold() for marker in telemetry_markers):
        return ""
    return reason


def _tutor_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "workspace_tutor_response",
            "strict": True,
            "schema": _TUTOR_OUTPUT_SCHEMA,
        },
    }


def _limit_phase_evidence_request(
    evidence_request: dict[str, Any] | None,
    *,
    phase: str,
    topic: str,
    language_code: str,
    learning_context: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(evidence_request, dict):
        return evidence_request
    request = dict(evidence_request)
    prompt = str(request.get("prompt") or "").strip()
    if _normalize_phase(phase) != "explore" or (
        len(prompt) <= 280 and prompt.count("?") <= 1
    ):
        return request
    request["prompt"] = _fallback_current_phase_request(
        phase=phase,
        topic=topic,
        language_code=language_code,
        learning_context=learning_context,
    )
    request["expected_evidence"] = (
        "one concrete numerical observation from the current experiment"
    )
    return request


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
    application_count = max(
        0,
        int(learning_context.get("elaborate_application_count") or 0),
    )
    application_step = application_count + 1
    if language_code == "id":
        prompts = {
            "engage": f"Bagaimana kamu sekarang menangani satu contoh sederhana {topic}, dan bagian mana yang masih membuatmu ragu?",
            "explore": (
                f"Tulis ekspresi tepat {topic} yang sedang kita gunakan. Lalu hitung satu "
                "nilai bagian dalam sebelum dan sesudah perubahan input kecil; ini menunjukkan "
                "perubahan yang diteruskan ke operasi berikutnya."
            ),
            "explain": (
                f"Pada ekspresi {topic} yang baru dibahas, sebutkan operasi pertama dan kedua, "
                "lalu jelaskan mengapa faktor perubahan keduanya digabungkan."
            ),
            "elaborate": (
                f"Latihan bertahap langkah {application_step}: terapkan konsep pada "
                "contoh baru ini dan tunjukkan alasan untuk setiap langkahmu."
            ),
            "evaluate": "Latihan workspace sudah selesai. Mulai posttest untuk evaluasi mandiri.",
        }
    else:
        prompts = {
            "engage": f"How would you currently handle one simple {topic} example, and where are you still unsure?",
            "explore": (
                f"Write the exact {topic} expression we are using. Then calculate one inner "
                "value before and after a small input change; this shows what change reaches "
                "the next operation."
            ),
            "explain": (
                f"For the {topic} expression just discussed, name the first and second "
                "operations, then explain why their change factors combine."
            ),
            "elaborate": (
                f"Guided practice step {application_step}: apply the concept to this "
                "new example and show the reason for each step."
            ),
            "evaluate": "The guided workspace is complete. Start the posttest for the independent evaluation.",
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
