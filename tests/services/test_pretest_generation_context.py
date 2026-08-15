from sqlalchemy import select

import pytest

from app.modules.curriculum.models import KnowledgeConcept
from app.modules.curriculum.seed import seed_curriculum
from app.modules.pretests.adaptive_service import (
    _localized_graph_scope,
    _pretest_skill_candidates,
    _select_pretest_diagnostic_path,
)
from app.modules.pretests.generation_service import (
    _fresh_generation_max_tokens,
    _fresh_generation_timeout_seconds,
    _fresh_question_prompt,
    _fresh_question_response_format,
    _fresh_question_type_choices,
    _max_generation_attempts,
    _normalize_fresh_question_payload,
    _normalize_skill_trace,
    _solution_skill_catalog,
    _validate_completed_reasoning,
    _validate_diagnostic_path_trace,
)
from app.modules.pretests.generation_service import QuestionGenerationPayloadError
from app.modules.pretests.graph_scope_builder import GraphScopeBuilder


def test_fresh_prompt_uses_curriculum_evidence_misconceptions_and_guidance():
    concept = KnowledgeConcept(
        code="curve.sketch",
        title="Sketsa kurva menggunakan turunan",
        id_desc="Analisis bentuk kurva menggunakan turunan pertama dan kedua.",
        metadata_json={
            "assessment_evidence_id": ["Membaca tanda f'(x)."],
            "common_misconceptions_id": ["Menganggap f'(x)=0 selalu ekstrem."],
            "question_generation_guidance_id": {
                "easy": "Minta satu fitur kurva.",
                "medium": "Gabungkan interval dan titik kritis.",
                "hard": "Verifikasi kandidat titik belok.",
            },
        },
    )

    prompt = _fresh_question_prompt(
        concept=concept,
        difficulties=["easy", "medium", "hard"],
        assessment_type="pretest",
        language="Indonesian",
        node_role="goal",
        skill_candidates=[
            {
                "concept_code": "chain.rule",
                "title": "Aturan rantai",
                "description": "Mendiferensiasikan fungsi komposit.",
            }
        ],
        diagnosis_context="",
        previous_questions=[],
    )

    assert "Membaca tanda f'(x)." in prompt
    assert "Menganggap f'(x)=0 selalu ekstrem." in prompt
    assert "- hard: Verifikasi kandidat titik belok." in prompt
    assert '"concept_code": "chain.rule"' in prompt
    assert "Build skill_trace from the actual steps" in prompt
    assert "A concept_code may appear at most once" in prompt
    assert "Incorrect — duplicate concept_code entries" in prompt
    assert "Correct — merge all steps belonging to that concept" in prompt
    assert "be mutually exclusive" in prompt
    assert "not merely less complete" in prompt
    assert "distractor_rationales" not in prompt
    assert "same answer dimension" in prompt
    assert "missing factor" in prompt
    assert "backend assigns and shuffles A/B/C/D labels" in prompt
    assert "never assign option labels yourself" in prompt
    assert "Never return self-correction, drafting notes" in prompt
    assert "finalizes immediately" not in prompt
    assert "Prerequisite checks happen only" not in prompt


def test_pretest_generation_allows_two_attempts(monkeypatch):
    monkeypatch.delenv("WICARA_PRETEST_LLM_MAX_ATTEMPTS", raising=False)

    assert _max_generation_attempts(assessment_type="pretest") == 2


def test_fresh_generation_uses_strict_batch_and_option_counts():
    response_format = _fresh_question_response_format(question_count=3)
    questions = response_format["json_schema"]["schema"]["properties"]["questions"]
    distractors = questions["items"]["properties"]["distractors"]

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert questions["minItems"] == questions["maxItems"] == 3
    assert distractors["minItems"] == distractors["maxItems"] == 3
    skill_trace = questions["items"]["properties"]["skill_trace"]
    assert skill_trace["minItems"] == 1
    assert questions["items"]["required"] == [
        "stem",
        "question_type",
        "correct_answer",
        "distractors",
        "skill_trace",
        "expected_reasoning",
        "explanation",
    ]
    assert {
        "language",
        "concept_code",
        "difficulty",
        "final_answer",
        "misconception_tags",
        "distractor_rationales",
        "difficulty_reason",
        "freshness_note",
    }.isdisjoint(questions["items"]["properties"])


def test_fresh_generation_output_budget_scales_with_batch_size():
    assert _fresh_generation_max_tokens(question_count=1) == 6000
    assert _fresh_generation_max_tokens(question_count=2) == 10000
    assert _fresh_generation_max_tokens(question_count=3) == 14000


def test_pretest_generation_timeout_is_capped_below_frontend_timeout(monkeypatch):
    monkeypatch.setenv("WICARA_PRETEST_LLM_TIMEOUT_SECONDS", "999")

    assert _fresh_generation_timeout_seconds(
        assessment_type="pretest",
        ai_request_timeout_seconds=999,
    ) == 270


def test_prerequisite_probe_schema_requires_direct_computation():
    types = _fresh_question_type_choices(
        difficulties=["easy", "medium", "hard"],
        node_role="prerequisite",
    )
    response_format = _fresh_question_response_format(
        question_count=1,
        question_types=types,
    )
    question_schema = response_format["json_schema"]["schema"]["properties"][
        "questions"
    ]["items"]

    assert question_schema["properties"]["question_type"]["enum"] == [
        "direct_computation",
    ]
    assert "correct_answer" in question_schema["required"]


def test_backend_assigns_labels_and_preserves_one_correct_answer():
    payload = {
        "stem": "Which statement is correct?",
        "question_type": "concept_application",
        "correct_answer": "Fourth statement",
        "distractors": [
            "First statement",
            "Second statement",
            "Third statement",
        ],
        "skill_trace": [{"concept_code": "curve.sketch", "criterion": "Check it."}],
        "expected_reasoning": "The fourth statement follows from the calculation.",
        "explanation": "The fourth statement is correct.",
    }
    question = _normalize_fresh_question_payload(
        payload,
        concept_code="curve.sketch",
        difficulty="medium",
    )

    assert {option["label"] for option in question["options"]} == {"A", "B", "C", "D"}
    assert [option["text"] for option in question["options"] if option["is_correct"]] == [
        "Fourth statement"
    ]


def test_unresolved_self_correction_is_rejected():
    question = {
        "expected_reasoning": (
            "The selected answer is inconsistent. I need to adjust the correct answer "
            "and the distractors need to be revised."
        ),
        "explanation": "Draft explanation.",
    }

    try:
        _validate_completed_reasoning([question])
    except QuestionGenerationPayloadError as error:
        assert "unresolved self-correction" in str(error)
    else:
        raise AssertionError("Unresolved model drafting notes should be rejected")


def test_reasoning_cannot_reference_backend_assigned_option_labels():
    question = {
        "expected_reasoning": "Pilihan C salah karena menggunakan turunan kedua.",
        "explanation": "Bandingkan tanda turunannya.",
    }

    with pytest.raises(
        QuestionGenerationPayloadError,
        match="must not discuss backend-assigned answer options",
    ):
        _validate_completed_reasoning([question])


def test_duplicate_skill_trace_entries_are_merged_by_concept_code():
    trace = _normalize_skill_trace(
        [
            {"concept_code": "curve.sketch", "criterion": "Find critical points."},
            {"concept_code": "curve.sketch", "criterion": "Classify the points."},
            {"concept_code": "chain.rule", "criterion": "Include the inner derivative."},
        ]
    )

    assert trace == [
        {
            "concept_code": "curve.sketch",
            "criterion": "Find critical points. Classify the points.",
        },
        {
            "concept_code": "chain.rule",
            "criterion": "Include the inner derivative.",
        },
    ]


def test_solution_skill_catalog_excludes_verbose_candidate_diagnostics():
    concept = KnowledgeConcept(
        code="curve.sketch",
        title="Curve sketch",
        description="Analyze curve behavior.",
    )

    catalog = _solution_skill_catalog(
        concept=concept,
        skill_candidates=[
            {
                "concept_code": "chain.rule",
                "title": "Chain rule",
                "description": "Differentiate composite functions.",
                "assessment_evidence": ["Long evidence that belongs to the candidate node."],
                "common_misconceptions": ["Long misconception that is not needed in this catalog."],
            }
        ],
    )

    assert catalog == [
        {
            "concept_code": "curve.sketch",
            "title": "Curve sketch",
            "description": "Analyze curve behavior.",
        },
        {
            "concept_code": "chain.rule",
            "title": "Chain rule",
            "description": "Differentiate composite functions.",
        },
    ]


def test_skill_candidates_include_reachable_nodes_without_auto_selecting_one():
    graph_scope = {
        "nodes": [
            {
                "concept_code": "curve.sketch",
                "concept_id": "target-id",
                "title": "Curve sketch",
                "description": "Analyze a curve.",
                "depth": 0,
                "role": "target",
                "parent": None,
            },
            {
                "concept_code": "derivative.algebra",
                "concept_id": "algebra-id",
                "title": "Algebraic differentiation",
                "description": "Compute derivatives algebraically.",
                "depth": 1,
                "role": "prerequisite",
                "parent": "curve.sketch",
            },
            {
                "concept_code": "trig.derivative",
                "concept_id": "trig-id",
                "title": "Trigonometric derivatives",
                "description": "Differentiate trigonometric functions.",
                "depth": 1,
                "role": "prerequisite",
                "parent": "curve.sketch",
            },
            {
                "concept_code": "chain.rule",
                "concept_id": "chain-id",
                "title": "Chain rule",
                "description": "Differentiate outer and inner functions.",
                "common_misconceptions": ["Forgetting the inner derivative."],
                "depth": 2,
                "role": "prerequisite",
                "parent": "trig.derivative",
            },
        ],
        "edges": [
            {
                "from": "curve.sketch",
                "to": "derivative.algebra",
                "weight": 0.9,
                "depth": 1,
                "reason": "Required to obtain f'(x) and f''(x).",
            },
            {
                "from": "curve.sketch",
                "to": "trig.derivative",
                "weight": 0.8,
                "depth": 1,
                "reason": "Used for trigonometric curve tasks.",
            },
            {
                "from": "trig.derivative",
                "to": "chain.rule",
                "weight": 0.76,
                "depth": 2,
                "reason": "Required when the trigonometric argument is composite.",
            },
        ],
    }

    candidates = _pretest_skill_candidates(
        graph_scope,
        concept_code="curve.sketch",
    )
    chain_candidate = next(
        candidate
        for candidate in candidates
        if candidate["concept_code"] == "chain.rule"
    )

    assert [item["concept_code"] for item in candidates] == [
        "derivative.algebra",
        "trig.derivative",
        "chain.rule",
    ]
    assert chain_candidate["common_misconceptions"] == [
        "Forgetting the inner derivative."
    ]


def test_revised_golden_scope_exposes_chain_rule_as_question_skill_candidate(db_session):
    seed_curriculum(db_session)
    target = db_session.scalar(
        select(KnowledgeConcept).where(
            KnowledgeConcept.code
            == "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan"
        )
    )
    graph_scope = GraphScopeBuilder().build(
        db_session,
        target_concept_id=target.id,
        max_depth=2,
    )
    localized_scope = _localized_graph_scope(
        db_session,
        graph_scope,
        language="id",
    )
    candidates = _pretest_skill_candidates(
        localized_scope,
        concept_code=target.code,
    )
    candidate_codes = {item["concept_code"] for item in candidates}
    chain = next(
        item
        for item in candidates
        if item["concept_code"]
        == "km_f_matematika_tingkat_lanjut_aturan_rantai"
    )

    assert "km_f_matematika_tingkat_lanjut_turunan_secara_aljabar" in candidate_codes
    assert "km_f_matematika_tingkat_lanjut_aturan_rantai" in candidate_codes
    assert any(
        "Hanya menurunkan fungsi luar" in misconception
        for misconception in chain["common_misconceptions"]
    )
    selected_path = sorted(
        (
            item["diagnostic_path_order"],
            item["concept_code"],
        )
        for item in candidates
        if item.get("diagnostic_path_order")
    )
    assert selected_path == [
        (1, "km_f_matematika_tingkat_lanjut_aturan_rantai"),
        (2, "km_f_matematika_tingkat_lanjut_turunan_fungsi_trigonometri"),
    ]


def test_goal_prompt_requires_question_specific_skill_trace_without_forced_focus():
    concept = KnowledgeConcept(
        code="curve.sketch",
        title="Curve sketch",
        en_desc="Analyze curve behavior using derivatives.",
        metadata_json={},
    )
    prompt = _fresh_question_prompt(
        concept=concept,
        difficulties=["easy", "medium", "hard"],
        assessment_type="pretest",
        language="English",
        node_role="goal",
        skill_candidates=[
            {
                "concept_code": "chain.rule",
                "title": "Chain rule",
                "description": "Differentiate composite functions.",
                "diagnostic_path_order": 1,
            }
        ],
        diagnosis_context="",
        previous_questions=[],
    )

    assert '"concept_code": "chain.rule"' in prompt
    assert "never add a skill merely to force adaptive routing" in prompt
    assert "skill_trace" in prompt
    assert "diagnostic_prerequisite_code" not in prompt
    assert "Graph-selected hard diagnostic path" in prompt
    assert "selected generically from curriculum structure" in prompt


def test_diagnostic_path_selection_uses_graph_depth_and_order_not_skill_names():
    scope = {
        "nodes": [
            {"concept_code": "target", "depth": 0, "display_order": 50},
            {"concept_code": "branch.low", "depth": 1, "display_order": 30},
            {"concept_code": "branch.high", "depth": 1, "display_order": 40},
            {"concept_code": "leaf.low", "depth": 2, "display_order": 10},
            {"concept_code": "leaf.high", "depth": 2, "display_order": 20},
        ],
        "edges": [
            {"from": "target", "to": "branch.low", "weight": 0.9},
            {"from": "target", "to": "branch.high", "weight": 0.8},
            {"from": "branch.low", "to": "leaf.low", "weight": 0.9},
            {"from": "branch.high", "to": "leaf.high", "weight": 0.8},
        ],
    }

    assert _select_pretest_diagnostic_path(scope, concept_code="target") == [
        "branch.high",
        "leaf.high",
    ]


def test_hard_trace_must_cover_generic_selected_path():
    candidates = [
        {"concept_code": "inner.skill", "diagnostic_path_order": 1},
        {"concept_code": "outer.skill", "diagnostic_path_order": 2},
    ]
    questions = [
        {
            "skill_trace": [
                {"concept_code": "inner.skill", "criterion": "Use inner skill."},
            ]
        }
    ]

    try:
        _validate_diagnostic_path_trace(
            questions,
            difficulties=["hard"],
            skill_candidates=candidates,
        )
    except QuestionGenerationPayloadError as error:
        assert "outer.skill" in str(error)
    else:
        raise AssertionError("A hard question missing a selected path skill must be rejected")
