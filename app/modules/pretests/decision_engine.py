from __future__ import annotations

from copy import deepcopy
from typing import Any

class PretestDecisionEngine:
    def record_attempt(
        self,
        state: dict[str, Any],
        *,
        concept_code: str,
        difficulty: str,
        is_correct: bool,
        evidence_score: float,
        confidence: float,
        answer_score: float | None = None,
        reasoning_score: float | None = None,
        canvas_score: float | None = None,
        diagnostic_signal: str = "",
        reasoning_signal: str = "",
        attempt_id: str | None = None,
        evidence_deferred: bool = False,
        method_valid: bool | None = None,
        evidence_tags: list[str] | None = None,
        suspected_prerequisite_code: str | None = None,
        method_reason: str = "",
        method_evaluation_source: str = "",
    ) -> dict[str, Any]:
        next_state = deepcopy(state)
        node_results = next_state.setdefault("node_results", {})
        node_state = node_results.setdefault(
            concept_code,
            {"status": "not_asked", "attempts": []},
        )
        node_state[difficulty] = "correct" if is_correct else "wrong"
        node_state["attempts"].append(
            {
                "difficulty": difficulty,
                "is_correct": is_correct,
                "answer_score": round(float(answer_score if answer_score is not None else (1.0 if is_correct else 0.0)), 4),
                "reasoning_score": round(float(reasoning_score), 4) if reasoning_score is not None else None,
                "canvas_score": round(float(canvas_score), 4) if canvas_score is not None else None,
                "evidence_score": round(float(evidence_score), 4),
                "confidence": round(float(confidence), 4),
                "diagnostic_signal": diagnostic_signal,
                "reasoning_signal": reasoning_signal,
                "attempt_id": attempt_id,
                "evidence_deferred": evidence_deferred,
                "method_valid": method_valid,
                "evidence_tags": list(evidence_tags or []),
                "suspected_prerequisite_code": suspected_prerequisite_code,
                "method_reason": method_reason,
                "method_evaluation_source": method_evaluation_source,
            }
        )
        node_state["status"] = _node_status(node_state)
        next_state["confidence"] = max(float(next_state.get("confidence", 0.0)), float(confidence))
        return next_state

    def decide(
        self,
        state: dict[str, Any],
        *,
        last_concept_code: str,
        last_difficulty: str,
        last_is_correct: bool,
        graph_scope: dict[str, Any],
        method_valid: bool | None = None,
        suspected_prerequisite_code: str | None = None,
        evidence_tags: list[str] | None = None,
        method_reason: str = "",
        source_attempt_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        next_state = deepcopy(state)
        limit_action = self._limit_action(next_state)
        if limit_action is not None:
            next_state["stop_reason"] = limit_action["reason"]
            return next_state, limit_action

        method_action = self._method_evidence_action(
            next_state,
            last_concept_code=last_concept_code,
            graph_scope=graph_scope,
            method_valid=method_valid,
            suspected_prerequisite_code=suspected_prerequisite_code,
            evidence_tags=evidence_tags or [],
            method_reason=method_reason,
            source_attempt_id=source_attempt_id,
        )
        if method_action is not None:
            return next_state, method_action

        target_code = str(next_state["target_concept_code"])
        if last_concept_code == target_code:
            return self._decide_target(
                next_state,
                last_difficulty=last_difficulty,
                last_is_correct=last_is_correct,
            )
        return self._decide_prerequisite(
            next_state,
            last_concept_code=last_concept_code,
            last_difficulty=last_difficulty,
            last_is_correct=last_is_correct,
        )

    def _method_evidence_action(
        self,
        state: dict[str, Any],
        *,
        last_concept_code: str,
        graph_scope: dict[str, Any],
        method_valid: bool | None,
        suspected_prerequisite_code: str | None,
        evidence_tags: list[str],
        method_reason: str,
        source_attempt_id: str | None,
    ) -> dict[str, Any] | None:
        if method_valid is not False:
            return None

        allowed_codes = {
            str(node.get("concept_code"))
            for node in graph_scope.get("nodes", [])
            if isinstance(node, dict)
            and str(node.get("concept_code") or "").strip()
        }
        visited = set(state.get("node_results", {}))
        candidate = (
            str(suspected_prerequisite_code)
            if suspected_prerequisite_code in allowed_codes
            else None
        )

        route = {
            "source_attempt_id": source_attempt_id,
            "from_concept_code": last_concept_code,
            "method_valid": False,
            "evidence_tags": list(evidence_tags),
            "reason": method_reason,
            "suspected_prerequisite_code": suspected_prerequisite_code,
            "routed_prerequisite_code": candidate,
        }
        state.setdefault("method_evidence_routes", []).append(route)
        if candidate is None or candidate == last_concept_code:
            return None
        if candidate in visited:
            state["stop_reason"] = "evidence_directed_gap_confirmed"
            return {"type": "finalize", "reason": state["stop_reason"]}
        return _ask(candidate, "medium", "evidence_directed_gap_probe")

    def _decide_target(
        self,
        state: dict[str, Any],
        *,
        last_difficulty: str,
        last_is_correct: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = str(state["target_concept_code"])
        if last_difficulty == "medium":
            if last_is_correct:
                return state, _ask(target, "hard", "target_medium_correct")
            return state, _ask(target, "easy", "target_medium_wrong")
        if last_difficulty == "hard":
            if last_is_correct:
                state["stop_reason"] = "target_ready"
                return state, {"type": "finalize", "reason": "target_ready"}
            target_state = state.get("node_results", {}).get(target, {})
            if "easy" not in target_state:
                return state, _ask(target, "easy", "target_gap_disambiguation")
            state["stop_reason"] = "target_gap_confirmed"
            return state, {"type": "finalize", "reason": state["stop_reason"]}
        if last_difficulty == "easy":
            state["stop_reason"] = "target_gap_confirmed"
            return state, {"type": "finalize", "reason": state["stop_reason"]}
        state["stop_reason"] = "unsupported_target_difficulty"
        return state, {"type": "finalize", "reason": "unsupported_target_difficulty"}

    def _decide_prerequisite(
        self,
        state: dict[str, Any],
        *,
        last_concept_code: str,
        last_difficulty: str,
        last_is_correct: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if last_difficulty == "medium":
            if last_is_correct:
                return state, _ask(
                    last_concept_code,
                    "hard",
                    "prerequisite_medium_correct",
                )
            return state, _ask(last_concept_code, "easy", "prerequisite_medium_wrong")
        if last_difficulty == "hard":
            state["stop_reason"] = "prerequisite_strength_checked"
            return state, {"type": "finalize", "reason": state["stop_reason"]}
        if last_difficulty == "easy":
            state["stop_reason"] = (
                "evidence_directed_gap_confirmed"
                if not last_is_correct
                else "prerequisite_fragility_confirmed"
            )
            return state, {"type": "finalize", "reason": state["stop_reason"]}
        state["stop_reason"] = "unsupported_prerequisite_difficulty"
        return state, {"type": "finalize", "reason": "unsupported_prerequisite_difficulty"}

    def _limit_action(self, state: dict[str, Any]) -> dict[str, Any] | None:
        if int(state.get("question_count", 0)) >= int(state.get("max_questions", 10)):
            return {"type": "finalize", "reason": "max_questions_reached"}
        if float(state.get("confidence", 0.0)) >= float(state.get("confidence_threshold", 0.8)):
            # Target mastery has explicit stop rules; confidence stops only after prerequisite probing starts.
            if str(state.get("current_concept_code")) != str(state.get("target_concept_code")):
                return {"type": "finalize", "reason": "confidence_threshold_reached"}
        return None


def _ask(concept_code: str, difficulty: str, reason: str) -> dict[str, Any]:
    return {
        "type": "next_question",
        "concept_code": concept_code,
        "difficulty": difficulty,
        "reason": reason,
    }


def _node_status(node_state: dict[str, Any]) -> str:
    medium = node_state.get("medium")
    hard = node_state.get("hard")
    easy = node_state.get("easy")
    if medium == "correct" and hard == "correct":
        status = "ready"
    elif medium == "correct" and hard == "wrong":
        status = "partial"
    elif medium == "wrong" and easy == "correct":
        status = "fragile"
    elif medium == "wrong" and easy == "wrong":
        status = "gap"
    elif medium == "correct":
        status = "probably_ready"
    elif medium == "wrong":
        status = "probably_gap"
    else:
        status = "not_asked"

    attempts = node_state.get("attempts")
    if isinstance(attempts, list):
        explicit_method_results = [
            attempt.get("method_valid")
            for attempt in attempts
            if isinstance(attempt, dict) and isinstance(attempt.get("method_valid"), bool)
        ]
        if explicit_method_results and explicit_method_results[-1] is False:
            if status in {"ready", "probably_ready"}:
                return "fragile"
    return status
