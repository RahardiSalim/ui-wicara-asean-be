from __future__ import annotations

from difflib import SequenceMatcher
import json
import logging
import re
from typing import Any

from app.modules.ai import ai_client
from app.modules.ai.errors import AIError
from app.modules.ai.schemas import AIGenerationResponse
from app.core.language import language_display_name, normalize_language_code
from app.modules.workspaces.models import WorkspaceEvent, WorkspaceSession
from app.modules.workspaces.schemas import TutorResponseRead

logger = logging.getLogger(__name__)

PROMPT_VERSION = "wicara_5e_profile_language_v3"
PHASE_SEQUENCE = ("engage", "explore", "explain", "elaborate", "evaluate")

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
    },
    "required": ["text", "next_phase_ready"],
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
- Never give away the full answer — lead the student to discover it.
- Be warm, encouraging, and precise.
- Avoid repeating the same opening pattern (for example repeated "Imagine..." hooks).
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
        "Give a clear explanation in {response_language}: what it is, why it matters, and one worked example. "
        "Keep it concise and concrete. End with one short check-in question."
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
) -> str:
    template = _PROMPTS.get(current_phase, _PROMPTS["chat"])
    next_phase = _next_phase(current_phase)
    transition_instruction = (
        "Phase transition check:\n"
        f"- Current phase: {current_phase}\n"
        f"- Next phase candidate: {next_phase if next_phase else '(none, final phase)'}\n"
        f"- Transition criteria: {_PHASE_TRANSITION_CRITERIA.get(current_phase, _PHASE_TRANSITION_CRITERIA['engage'])}\n"
        "- Set next_phase_ready=true only if the learner is pedagogically ready for the next phase.\n"
        "- If current phase is evaluate, always return next_phase_ready=false.\n\n"
        "Output format requirement:\n"
        "Return JSON object with keys exactly: text (string), next_phase_ready (boolean), phase_reasoning (string)."
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
) -> tuple[TutorResponseRead | None, dict[str, Any]]:
    """
    Call the configured AI provider to generate a tutor response.
    Returns (TutorResponseRead | None, audit_metadata).
    Falls back to deterministic response if AI generation fails.
    Only returns a response for event types that warrant one.
    """
    if event_type not in {"text", "quiz_answer", "canvas_sent"}:
        return None, {"ai_source": "skipped", "reason": f"no_response_for_{event_type}"}

    topic = workspace.current_topic or "this module"
    history = _build_history(events)
    phase = _normalize_phase(current_phase)
    language_code, response_language, language_source = _resolve_response_language(
        learner_language=learner_language,
        latest_message=text_payload,
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
    )

    audit: dict[str, Any] = {
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
    }

    try:
        ai_response: AIGenerationResponse = await ai_client.generate(
            system_instruction=_SYSTEM_INSTRUCTION,
            user_instruction=user_instruction,
            params={
                "response_format": {"type": "json_object"},
            },
        )
        audit.update(
            {
                "ai_source": ai_response.provider,
                "ai_provider": ai_response.provider,
                "ai_model": ai_response.model,
                "finish_reason": ai_response.finish_reason,
                "input_tokens": ai_response.usage.input_tokens if ai_response.usage else None,
                "output_tokens": ai_response.usage.output_tokens if ai_response.usage else None,
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
        ), audit

    except AIError as exc:
        logger.warning("AI tutor call failed, using deterministic fallback: %s", exc)
        audit["ai_source"] = "deterministic_fallback"
        audit["fallback_reason"] = str(exc)
        return _fallback_response(
            event_type,
            language_code=language_code,
            current_phase=phase,
        ), audit


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
    topic_text = _clean_topic_for_tutor_text(topic)
    if language_code == "id":
        return (
            f"Halo, siap. Kita mulai dari {topic_text}. Bagian mana yang paling ingin kamu cek dulu?"
        )
    return f"Hi, ready to start. Let us begin with {topic_text}. Which part do you want to check first?"


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


def _anti_repeat_response(*, language_code: str, phase: str, student_message: str, topic: str) -> str:
    message = student_message.strip()
    topic_text = _clean_topic_for_tutor_text(topic)
    if language_code == "id":
        if phase == "engage":
            return (
                f"Mantap, kita fokus ke {topic_text}. Bagian mana yang paling bikin bingung?"
            )
        if phase == "explore":
            return (
                f"Oke, uji cepat: sebutkan satu contoh dari {topic_text} yang menurutmu paling mudah dicek."
            )
        if phase == "explain":
            return (
                "Bagus. Coba jelaskan lagi dengan kata-katamu sendiri, lalu beri 1 contoh singkat."
            )
        if phase == "elaborate":
            return (
                f"Lanjut latihan: terapkan {topic_text} ke satu kasus baru, lalu jelaskan langkah pertamamu."
            )
        if phase == "evaluate":
            return (
                "Jawabanmu sudah dicatat. Coba cek lagi bagian yang paling ragu, lalu perbaiki satu langkah."
            )
        return (
            "Masuk. Lanjutkan dari poin terakhirmu dan jelaskan satu langkah berikutnya."
        )
    if phase == "engage":
        return (
            f"Great, let us focus on {topic_text}. Which part feels most confusing?"
        )
    if phase == "explore":
        return (
            f"Quick check: name one example from {topic_text} that feels easiest to verify."
        )
    if phase == "explain":
        return "Nice. Restate the idea in your own words and give one short example."
    if phase == "elaborate":
        return f"Try applying {topic_text} to one new case, then explain your first step briefly."
    if phase == "evaluate":
        return (
            "I noted your answer. Recheck the step you are least sure about and revise it once."
        )
    if message:
        return "Good point. Continue from your last step and add one more concrete step."
    return "Good point. Add one concrete next step."


def _clean_topic_for_tutor_text(topic: str) -> str:
    normalized = re.sub(r"\s+", " ", str(topic or "").strip())
    return normalized if normalized else "this topic"


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
        return {
            "text": "",
            "next_phase_ready": False,
            "phase_reasoning": None,
            "parse_ok": False,
        }

    payload = _parse_json_payload(text)
    if payload is None:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            payload = _parse_json_payload(match.group(0))
    if payload is None:
        return {
            "text": text,
            "next_phase_ready": False,
            "phase_reasoning": None,
            "parse_ok": False,
        }

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
    return {
        "text": parsed_text,
        "next_phase_ready": next_phase_ready,
        "phase_reasoning": phase_reasoning,
        "parse_ok": True,
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
