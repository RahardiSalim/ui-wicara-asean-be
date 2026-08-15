from __future__ import annotations

import pytest

from app.modules.pretests.decision_engine import PretestDecisionEngine
from app.modules.pretests.graph_scope_builder import GraphScopeBuilder


TARGET = "baseline"
PREREQUISITE_1 = "prerequisite.1"
PREREQUISITE_2 = "prerequisite.2"


def _graph_scope() -> dict[str, object]:
    return {
        "nodes": [
            {
                "concept_code": TARGET,
                "concept_id": "target-id",
                "role": "target",
                "depth": 0,
                "parent": None,
            },
            {
                "concept_code": PREREQUISITE_1,
                "concept_id": "p1-id",
                "role": "prerequisite",
                "depth": 1,
                "parent": TARGET,
            },
            {
                "concept_code": PREREQUISITE_2,
                "concept_id": "p2-id",
                "role": "prerequisite",
                "depth": 2,
                "parent": PREREQUISITE_1,
            },
        ],
        "edges": [
            {
                "from": TARGET,
                "to": PREREQUISITE_1,
                "weight": 0.9,
            },
            {
                "from": PREREQUISITE_1,
                "to": PREREQUISITE_2,
                "weight": 0.8,
            },
        ],
    }


def _state() -> dict[str, object]:
    graph_scope = _graph_scope()
    return {
        "target_concept_code": TARGET,
        "current_concept_code": TARGET,
        "current_difficulty": "medium",
        "question_count": 1,
        "max_questions": 10,
        "max_nodes_visited": 5,
        "confidence": 0.0,
        "confidence_threshold": 0.95,
        "probe_queue": GraphScopeBuilder().build_probe_queue(graph_scope),
        "node_results": {},
    }


def _decide(
    state: dict[str, object],
    *,
    concept_code: str,
    difficulty: str,
    is_correct: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    return PretestDecisionEngine().decide(
        state,
        last_concept_code=concept_code,
        last_difficulty=difficulty,
        last_is_correct=is_correct,
        graph_scope=_graph_scope(),
    )


def test_baseline_medium_correct_asks_hard() -> None:
    _, action = _decide(
        _state(),
        concept_code=TARGET,
        difficulty="medium",
        is_correct=True,
    )

    assert action == {
        "type": "next_question",
        "concept_code": TARGET,
        "difficulty": "hard",
        "reason": "target_medium_correct",
    }


@pytest.mark.parametrize(
    ("is_correct", "reason"),
    [(True, "target_ready"), (False, "target_reinforcement")],
)
def test_baseline_hard_always_finalizes(
    is_correct: bool,
    reason: str,
) -> None:
    _, action = _decide(
        _state(),
        concept_code=TARGET,
        difficulty="hard",
        is_correct=is_correct,
    )

    assert action == {"type": "finalize", "reason": reason}


@pytest.mark.parametrize("easy_is_correct", [True, False])
def test_baseline_medium_wrong_then_easy_enters_first_prerequisite(
    easy_is_correct: bool,
) -> None:
    state = _state()
    _, medium_action = _decide(
        state,
        concept_code=TARGET,
        difficulty="medium",
        is_correct=False,
    )
    state["node_results"] = {
        TARGET: {"medium": "wrong", "easy": "correct" if easy_is_correct else "wrong"}
    }

    _, easy_action = _decide(
        state,
        concept_code=TARGET,
        difficulty="easy",
        is_correct=easy_is_correct,
    )

    assert medium_action["difficulty"] == "easy"
    assert easy_action == {
        "type": "next_question",
        "concept_code": PREREQUISITE_1,
        "difficulty": "medium",
        "reason": "enter_prerequisite_node",
    }


@pytest.mark.parametrize("easy_is_correct", [True, False])
def test_prerequisite_medium_wrong_then_easy_enters_next_prerequisite(
    easy_is_correct: bool,
) -> None:
    state = _state()
    state["probe_queue"] = state["probe_queue"][1:]
    state["current_concept_code"] = PREREQUISITE_1
    state["node_results"] = {
        TARGET: {"medium": "wrong", "easy": "wrong"},
        PREREQUISITE_1: {
            "medium": "wrong",
            "easy": "correct" if easy_is_correct else "wrong",
        },
    }

    _, medium_action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="medium",
        is_correct=False,
    )
    _, easy_action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="easy",
        is_correct=easy_is_correct,
    )

    assert medium_action["difficulty"] == "easy"
    assert easy_action["concept_code"] == PREREQUISITE_2
    assert easy_action["difficulty"] == "medium"


def test_failed_evidence_directed_medium_probe_confirms_gap() -> None:
    state = _state()
    state["current_concept_code"] = PREREQUISITE_1
    state["method_evidence_routes"] = [
        {
            "from_concept_code": TARGET,
            "routed_prerequisite_code": PREREQUISITE_1,
            "reason": "The learner omitted a required solution step.",
        }
    ]

    next_state, action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="medium",
        is_correct=False,
    )

    assert action == {
        "type": "finalize",
        "reason": "evidence_directed_gap_confirmed",
    }
    assert next_state["stop_reason"] == "evidence_directed_gap_confirmed"


@pytest.mark.parametrize("hard_is_correct", [True, False])
def test_prerequisite_medium_correct_then_hard_finalizes(
    hard_is_correct: bool,
) -> None:
    state = _state()
    state["current_concept_code"] = PREREQUISITE_1

    _, medium_action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="medium",
        is_correct=True,
    )
    _, hard_action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="hard",
        is_correct=hard_is_correct,
    )

    assert medium_action["difficulty"] == "hard"
    assert hard_action == {
        "type": "finalize",
        "reason": "prerequisite_strength_checked",
    }


def test_question_cap_stops_before_an_eleventh_question() -> None:
    state = _state()
    state["question_count"] = 10

    _, action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="easy",
        is_correct=False,
    )

    assert action == {"type": "finalize", "reason": "max_questions_reached"}


def test_high_confidence_does_not_interrupt_prerequisite_cycle() -> None:
    state = _state()
    state["current_concept_code"] = PREREQUISITE_1
    state["confidence"] = 0.99

    _, action = _decide(
        state,
        concept_code=PREREQUISITE_1,
        difficulty="medium",
        is_correct=False,
    )

    assert action["type"] == "next_question"
    assert action["difficulty"] == "easy"
