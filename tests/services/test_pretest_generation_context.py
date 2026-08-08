from sqlalchemy import select

from app.modules.curriculum.models import KnowledgeConcept
from app.modules.curriculum.seed import seed_curriculum
from app.modules.pretests.adaptive_service import (
    _localized_graph_scope,
    _pretest_skill_candidates,
)
from app.modules.pretests.generation_service import (
    _fresh_generation_max_tokens,
    _fresh_question_prompt,
    _fresh_question_response_format,
    _fresh_question_type_choices,
    _max_generation_attempts,
    _normalize_skill_trace,
)
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
    assert "finalizes immediately" not in prompt
    assert "Prerequisite checks happen only" not in prompt


def test_pretest_generation_allows_two_attempts(monkeypatch):
    monkeypatch.delenv("WICARA_PRETEST_LLM_MAX_ATTEMPTS", raising=False)

    assert _max_generation_attempts(assessment_type="pretest") == 2


def test_fresh_generation_uses_strict_batch_and_option_counts():
    response_format = _fresh_question_response_format(question_count=3)
    questions = response_format["json_schema"]["schema"]["properties"]["questions"]
    options = questions["items"]["properties"]["options"]

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert questions["minItems"] == questions["maxItems"] == 3
    assert options["minItems"] == options["maxItems"] == 4
    skill_trace = questions["items"]["properties"]["skill_trace"]
    assert skill_trace["minItems"] == 1
    assert questions["items"]["required"] == [
        "stem",
        "question_type",
        "options",
        "correct_option_id",
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


def test_prerequisite_probe_schema_excludes_direct_computation():
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
        "error_analysis",
    ]
    assert "correct_option_id" in question_schema["required"]


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
            }
        ],
        diagnosis_context="",
        previous_questions=[],
    )

    assert '"concept_code": "chain.rule"' in prompt
    assert "never add a skill merely to force adaptive routing" in prompt
    assert '"skill_trace"' in prompt
    assert "diagnostic_prerequisite_code" not in prompt
