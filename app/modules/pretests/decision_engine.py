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
                graph_scope=graph_scope,
            )
        return self._decide_prerequisite(
            next_state,
            last_concept_code=last_concept_code,
            last_difficulty=last_difficulty,
            last_is_correct=last_is_correct,
            graph_scope=graph_scope,
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
        graph_scope: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = str(state["target_concept_code"])
        if last_difficulty == "medium":
            if last_is_correct:
                return state, _ask(target, "hard", "target_medium_correct")
            return state, _ask(target, "easy", "target_medium_wrong")
        if last_difficulty == "hard":
            state["stop_reason"] = (
                "target_ready" if last_is_correct else "target_reinforcement"
            )
            return state, {"type": "finalize", "reason": state["stop_reason"]}
        if last_difficulty == "easy":
            return self._ask_next_prerequisite(
                state,
                graph_scope=graph_scope,
                fallback_reason="target_basic_checked",
            )
        state["stop_reason"] = "unsupported_target_difficulty"
        return state, {"type": "finalize", "reason": "unsupported_target_difficulty"}

    def _decide_prerequisite(
        self,
        state: dict[str, Any],
        *,
        last_concept_code: str,
        last_difficulty: str,
        last_is_correct: bool,
        graph_scope: dict[str, Any],
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
            if not last_is_correct:
                self._boost_direct_prerequisites(
                    state,
                    graph_scope=graph_scope,
                    concept_code=last_concept_code,
                )
            return self._ask_next_prerequisite(
                state,
                graph_scope=graph_scope,
                fallback_reason=(
                    "root_fragility_found" if last_is_correct else "root_gap_found"
                ),
            )
        state["stop_reason"] = "unsupported_prerequisite_difficulty"
        return state, {"type": "finalize", "reason": "unsupported_prerequisite_difficulty"}

    def _ask_next_prerequisite(
        self,
        state: dict[str, Any],
        *,
        graph_scope: dict[str, Any],
        fallback_reason: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        queue = [
            deepcopy(item)
            for item in state.get("probe_queue", [])
            if isinstance(item, dict)
        ]
        visited = set(state.get("node_results", {}))
        visited.add(str(state.get("target_concept_code") or ""))

        while queue:
            queue.sort(key=_probe_sort_key)
            candidate = queue.pop(0)
            concept_code = str(candidate.get("concept_code") or "").strip()
            if not concept_code or concept_code in visited:
                continue
            if len(visited) >= int(state.get("max_nodes_visited", 5)):
                state["probe_queue"] = queue
                state["stop_reason"] = "max_nodes_visited"
                return state, {"type": "finalize", "reason": state["stop_reason"]}
            state["probe_queue"] = queue
            return state, _ask(
                concept_code,
                "medium",
                "enter_prerequisite_node",
            )

        state["probe_queue"] = []
        state["stop_reason"] = (
            fallback_reason if graph_scope.get("nodes") else "graph_exhausted"
        )
        return state, {"type": "finalize", "reason": state["stop_reason"]}

    def _boost_direct_prerequisites(
        self,
        state: dict[str, Any],
        *,
        graph_scope: dict[str, Any],
        concept_code: str,
    ) -> None:
        queue = [
            deepcopy(item)
            for item in state.get("probe_queue", [])
            if isinstance(item, dict)
        ]
        queued = {
            str(item.get("concept_code") or ""): item
            for item in queue
        }
        visited = set(state.get("node_results", {}))
        for prerequisite in _direct_prerequisites(
            graph_scope,
            concept_code=concept_code,
        ):
            code = str(prerequisite["concept_code"])
            if code in visited:
                continue
            existing = queued.get(code)
            if existing is None:
                queue.append(prerequisite)
                queued[code] = prerequisite
            else:
                existing["priority"] = max(
                    float(existing.get("priority", 0.0)),
                    float(prerequisite["priority"]),
                )
        state["probe_queue"] = queue

    def _limit_action(self, state: dict[str, Any]) -> dict[str, Any] | None:
        if int(state.get("question_count", 0)) >= int(state.get("max_questions", 10)):
            return {"type": "finalize", "reason": "max_questions_reached"}
        return None


def _ask(concept_code: str, difficulty: str, reason: str) -> dict[str, Any]:
    return {
        "type": "next_question",
        "concept_code": concept_code,
        "difficulty": difficulty,
        "reason": reason,
    }


def _probe_sort_key(item: dict[str, Any]) -> tuple[float, int, str]:
    return (
        -float(item.get("priority") or 0.0),
        int(item.get("depth") or 0),
        str(item.get("concept_code") or ""),
    )


def _direct_prerequisites(
    graph_scope: dict[str, Any],
    *,
    concept_code: str,
) -> list[dict[str, Any]]:
    nodes_by_code = {
        str(node.get("concept_code") or ""): node
        for node in graph_scope.get("nodes", [])
        if isinstance(node, dict)
    }
    prerequisites: list[dict[str, Any]] = []
    for edge in graph_scope.get("edges", []):
        if not isinstance(edge, dict) or str(edge.get("from") or "") != concept_code:
            continue
        node = nodes_by_code.get(str(edge.get("to") or ""))
        if node is None:
            continue
        depth = int(node.get("depth") or 1)
        weight = float(edge.get("weight") or 1.0)
        prerequisites.append(
            {
                "concept_code": str(node.get("concept_code") or ""),
                "concept_id": str(node.get("concept_id") or ""),
                "depth": depth,
                "priority": round(weight - (depth * 0.2) + 0.35, 4),
                "parent": concept_code,
            }
        )
    prerequisites.sort(key=_probe_sort_key)
    return prerequisites


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
