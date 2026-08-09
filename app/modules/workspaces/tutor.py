from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import json
import logging
import os
import re
from typing import Any, NamedTuple

from app.modules.ai import ai_client
from app.modules.ai.errors import AIError
from app.modules.ai.schemas import AIGenerationResponse
from app.core.language import language_display_name, normalize_language_code
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead

logger = logging.getLogger(__name__)


class TutorImageInput(NamedTuple):
    """A learner-supplied image (canvas snapshot / photo) to show the tutor."""

    file_path: str
    mime_type: str


PROMPT_VERSION = "wicara_5e_evidence_context_v4"
PHASE_SEQUENCE = ("engage", "explore", "explain", "elaborate", "evaluate")
DEFAULT_TUTOR_TIMEOUT_SECONDS = 20.0
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
        "Learner has shown initial curiosity or prior knowledge related to the topic, "
        "and is ready to do a discovery task."
    ),
    "explore": (
        "Learner has attempted exploration/discovery and shared observations, "
        "so they are ready for explicit explanation."
    ),
    "explain": (
        "Learner can restate the key concept and connect it to at least one worked idea/example, "
        "so they are ready for application."
    ),
    "elaborate": (
        "Learner can apply the concept to a new/contextualized case with reasonable reasoning, "
        "so they are ready for evaluation."
    ),
    "evaluate": (
        "Final stage. Keep evaluating understanding and giving feedback."
    ),
}

_TUTOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "next_phase_ready": {"type": "boolean"},
        "phase_reasoning": {"type": "string"},
        "evidence_tags": {"type": "array", "items": {"type": "string"}},
        "correctness": {"type": "string"},
        "misconception_status": {"type": "string"},
        "confidence": {"type": "number"},
        "evaluation_outcome": {"type": ["string", "null"]},
        "evidence_request": {"type": ["object", "null"]},
        "explanation_card": {"type": ["object", "null"]},
    },
    "required": [
        "text",
        "next_phase_ready",
        "evidence_tags",
        "correctness",
        "misconception_status",
        "confidence",
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
- End with one guiding question or clear next action.
- Lead the student to discover the answer. Obey the scaffold policy supplied with each
  turn: it states the current backend scaffold level and what you may reveal at it.
- Be warm, encouraging, and precise.
- Avoid repeating the same opening pattern (for example repeated "Imagine..." hooks).
- Treat the supplied learning context as authoritative. Ground the activity in the
  diagnosed evidence and remember the learner's original target.
- Report evidence only when the latest learner message actually demonstrates it.
- Never claim mastery merely because the learner says they understand or watches media.
- When an image accompanies the turn it is the learner's own drawing or worked solution:
  read it, refer to something concrete you can actually see in it, and judge correctness
  from the work shown. Never claim to have seen a drawing when no image was supplied.
""".strip()

_PROMPTS: dict[str, str] = {
    "engage": (
        "Topic: {topic}\n"
        "Stage: Engage\n"
        "Conversation so far:\n{history}\n\n"
        "Student latest message: {message}\n\n"
        "Respond in {response_language} with 1-2 short sentences.\n"
        "If this is the first engage turn, use one brief real-world hook.\n"
        "If this is not the first engage turn, do NOT start a new generic scenario; directly respond to the student's message.\n"
        "End with one focused question to activate prior knowledge.\n"
        "Do NOT explain the full concept yet."
    ),
    "explore": (
        "Topic: {topic}\n"
        "Stage: Explore\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "Give one probing challenge or mini experiment in {response_language} that pushes discovery. "
        "Keep it 1-2 sentences and avoid repeating prior tutor wording."
    ),
    "explain": (
        "Topic: {topic}\n"
        "Stage: Explain\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "First elicit the learner's explanation in their own words. Only after the "
        "learning context shows learner_explanation evidence, give a concise grounded "
        "formal explanation and a micro-check. Do not skip learner articulation."
    ),
    "elaborate": (
        "Topic: {topic}\n"
        "Stage: Elaborate\n"
        "Conversation so far:\n{history}\n\n"
        "Student: {message}\n\n"
        "Give one application task in {response_language} that makes the student apply what they learned. "
        "Keep it 2-3 sentences and tie it to the student's latest message."
    ),
    "evaluate": (
        "Topic: {topic}\n"
        "Stage: Evaluate\n"
        "Conversation so far:\n{history}\n\n"
        "Student answer: {message}\n\n"
        "Respond in {response_language}. "
        "If correct or partially correct: affirm and correct gently, suggest a next step. "
        "If incorrect: give a hint without revealing the answer. Ask them to try again. "
        "Keep it to 2-3 sentences and avoid repeating old feedback text."
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
        "Evidence contract:\n"
        f"- Allowed evidence_tags: {', '.join(sorted(_ALLOWED_EVIDENCE_TAGS))}.\n"
        "- correctness: correct|partial|incorrect|unknown.\n"
        "- misconception_status: none|suspected|active|resolved.\n"
        "- In Evaluate, evaluation_outcome is passed only with an independent attempt, "
        "error analysis, and reflection; otherwise use partial, misconception, or continue.\n"
        "- evidence_request describes the next task/tool but must not claim a result.\n"
        "- explanation_card is allowed only in Explain after learner_explanation evidence.\n\n"
        "Output format requirement:\n"
        "Return one JSON object with keys: text, next_phase_ready, phase_reasoning, "
        "evidence_tags, correctness, misconception_status, confidence, "
        "evaluation_outcome, evidence_request, explanation_card."
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
        }
    )

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
    for attempt in range(1, _TUTOR_MAX_ATTEMPTS + 1):
        try:
            ai_response: AIGenerationResponse = await asyncio.wait_for(
                ai_client.generate(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    user_instruction=user_instruction,
                    inputs=ai_inputs,
                    params={
                        "response_format": {"type": "json_object"},
                    },
                ),
                timeout=_tutor_timeout_seconds(),
            )
        except (AIError, TimeoutError) as exc:
            last_error = exc
            logger.warning(
                "AI tutor attempt %s/%s failed: %s", attempt, _TUTOR_MAX_ATTEMPTS, exc
            )
            if attempt < _TUTOR_MAX_ATTEMPTS:
                await asyncio.sleep(_TUTOR_RETRY_BACKOFF_SECONDS)
            continue

        audit.update(
            {
                "ai_source": ai_response.provider,
                "ai_provider": ai_response.provider,
                "ai_model": ai_response.model,
                "finish_reason": ai_response.finish_reason,
                "input_tokens": ai_response.usage.input_tokens if ai_response.usage else None,
                "output_tokens": ai_response.usage.output_tokens if ai_response.usage else None,
                "attempts": attempt,
            }
        )
        parsed = _parse_structured_tutor_output(ai_response.text)
        tutor_text = parsed["text"].strip()
        next_phase_ready = parsed["next_phase_ready"]
        phase_reasoning = parsed["phase_reasoning"]
        if not tutor_text:
            tutor_text = _fallback_text(event_type, language_code=language_code)
            next_phase_ready = False
            phase_reasoning = "fallback_due_to_empty_text"
            audit["ai_source"] = "ai_empty_fallback"
        tutor_text = _enforce_brevity(tutor_text, phase=phase)
        previous_tutor_text = _latest_tutor_text(events)
        if _is_repetitive_response(tutor_text, previous_tutor_text):
            tutor_text = _anti_repeat_response(
                language_code=language_code,
                phase=phase,
                student_message=text_payload,
                topic=topic,
            )
            audit["anti_repeat_fallback"] = True
        audit["structured_parse_ok"] = parsed["parse_ok"]
        if not parsed["parse_ok"]:
            audit["structured_parse_fallback"] = True
        return TutorResponseRead(
            text=tutor_text,
            intent=_STAGE_INTENT.get(phase, "ask_followup"),
            next_actions=_STAGE_ACTIONS.get(phase, ["ask_followup"]),
            next_phase_ready=bool(next_phase_ready) if phase != "evaluate" else False,
            phase_reasoning=phase_reasoning,
            evidence_tags=parsed["evidence_tags"],
            correctness=parsed["correctness"],
            misconception_status=parsed["misconception_status"],
            confidence=parsed["confidence"],
            evaluation_outcome=parsed["evaluation_outcome"],
            evidence_request=parsed["evidence_request"],
            explanation_card=parsed["explanation_card"],
        ), audit

    logger.warning(
        "AI tutor exhausted %s attempts, using deterministic fallback: %s",
        _TUTOR_MAX_ATTEMPTS,
        last_error,
    )
    audit["ai_source"] = "deterministic_fallback"
    audit["fallback_reason"] = str(last_error)
    audit["attempts"] = _TUTOR_MAX_ATTEMPTS
    audit["degraded"] = True
    return _fallback_response(
        event_type,
        language_code=language_code,
        current_phase=phase,
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
            "explore": "Coba satu pendekatan berbeda dan sebutkan pola yang kamu temukan.",
            "explain": "Jelaskan idenya dengan kata-katamu sendiri, lalu beri satu alasan.",
            "elaborate": "Terapkan ide yang sama pada situasi baru dan jelaskan perubahan langkahnya.",
            "evaluate": "Periksa langkah yang paling kamu ragukan, lalu revisi dengan alasan.",
        }
        return prompts.get(
            phase,
            "Lanjutkan dari poin terakhirmu dan tambahkan satu langkah konkret."
            if has_message
            else "Tambahkan satu langkah konkret.",
        )
    prompts = {
        "engage": f"Let's focus on your answer about {topic}. Which part would you test first?",
        "explore": "Try a different approach and name the pattern you observe.",
        "explain": "State the idea in your own words and give one reason.",
        "elaborate": "Apply the idea to a new situation and explain what changes.",
        "evaluate": "Recheck the step you trust least, then revise it with a reason.",
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
    return {
        "text": parsed_text,
        "next_phase_ready": next_phase_ready,
        "phase_reasoning": phase_reasoning,
        "evidence_tags": evidence_tags,
        "correctness": correctness,
        "misconception_status": misconception_status,
        "confidence": round(confidence, 4),
        "evaluation_outcome": evaluation_outcome,
        "evidence_request": evidence_request,
        "explanation_card": explanation_card,
        "parse_ok": True,
    }


def _unverified_tutor_payload(*, text: str) -> dict[str, Any]:
    return {
        "text": text,
        "next_phase_ready": False,
        "phase_reasoning": None,
        "evidence_tags": [],
        "correctness": "unknown",
        "misconception_status": "none",
        "confidence": 0.0,
        "evaluation_outcome": None,
        "evidence_request": None,
        "explanation_card": None,
        "parse_ok": False,
    }


def _parse_json_payload(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
