from sqlalchemy import select

from app.modules.curriculum.models import KnowledgeConcept
from app.modules.curriculum.seed import seed_curriculum
from app.modules.pretests.adaptive_service import (
    _localized_graph_scope,
    _pretest_diagnostic_focus,
    _pretest_prerequisite_context,
)
from app.modules.pretests.evidence_evaluator import _prerequisite_candidates
from app.modules.pretests.generation_service import (
    _fresh_question_prompt,
    _fresh_question_response_format,
    _max_generation_attempts,
)
from app.modules.pretests.graph_scope_builder import GraphScopeBuilder, direct_prerequisites


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
        prerequisite_context="- [conditional] chain.rule",
        diagnostic_focus=None,
        diagnosis_context="",
        previous_questions=[],
    )

    assert "Membaca tanda f'(x)." in prompt
    assert "Menganggap f'(x)=0 selalu ekstrem." in prompt
    assert "- hard: Verifikasi kandidat titik belok." in prompt
    assert "- [conditional] chain.rule" in prompt
    assert "be mutually exclusive" in prompt
    assert "not merely less complete" in prompt
    assert "admits another option is also correct" in prompt
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


def test_conditional_prerequisite_is_context_and_evaluator_candidate_not_generic_probe():
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
                "applicability": "required",
                "reason": "Required to obtain f'(x) and f''(x).",
            },
            {
                "from": "trig.derivative",
                "to": "chain.rule",
                "weight": 0.76,
                "depth": 2,
                "applicability": "conditional",
                "reason": "Required when the trigonometric argument is composite.",
            },
        ],
    }

    queue = GraphScopeBuilder.build_probe_queue(graph_scope)
    direct = direct_prerequisites(graph_scope, concept_code="trig.derivative")
    context = _pretest_prerequisite_context(
        graph_scope,
        concept_code="trig.derivative",
    )
    candidates = _prerequisite_candidates(graph_scope)
    chain_candidate = next(
        candidate
        for candidate in candidates
        if candidate["concept_code"] == "chain.rule"
    )

    assert [item["concept_code"] for item in queue] == ["derivative.algebra"]
    assert direct == []
    assert "[conditional] chain.rule" in context
    assert "trigonometric argument is composite" in context
    assert chain_candidate["common_misconceptions"] == [
        "Forgetting the inner derivative."
    ]
    assert chain_candidate["relationships"][0]["applicability"] == "conditional"


def test_revised_golden_scope_exposes_chain_rule_without_generic_chain_probe(db_session):
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
    diagnostic_focus = _pretest_diagnostic_focus(
        localized_scope,
        concept_code=target.code,
        node_role="goal",
    )
    queue_codes = {
        item["concept_code"]
        for item in GraphScopeBuilder.build_probe_queue(localized_scope)
    }
    context = _pretest_prerequisite_context(
        localized_scope,
        concept_code=target.code,
        diagnostic_focus=diagnostic_focus,
    )

    assert diagnostic_focus is not None
    assert diagnostic_focus["concept_code"] == (
        "km_f_matematika_tingkat_lanjut_aturan_rantai"
    )
    assert "km_f_matematika_tingkat_lanjut_turunan_secara_aljabar" in queue_codes
    assert "km_f_matematika_tingkat_lanjut_aturan_rantai" not in queue_codes
    assert (
        "[selected hidden diagnostic] "
        "km_f_matematika_tingkat_lanjut_aturan_rantai"
    ) in context
    assert "argumen sinus, kosinus, atau tangen" in context
    assert "Hanya menurunkan fungsi luar" in context


def test_goal_prompt_requires_hard_question_to_use_selected_diagnostic_focus():
    concept = KnowledgeConcept(
        code="curve.sketch",
        title="Curve sketch",
        en_desc="Analyze curve behavior using derivatives.",
        metadata_json={},
    )
    focus = {
        "concept_code": "chain.rule",
        "title": "Chain rule",
        "capability": "Differentiate composite functions.",
        "condition": "Use when the inner argument is a function.",
        "misconceptions": "Forgetting the inner derivative.",
    }

    prompt = _fresh_question_prompt(
        concept=concept,
        difficulties=["easy", "medium", "hard"],
        assessment_type="pretest",
        language="English",
        node_role="goal",
        prerequisite_context="- [conditional] chain.rule",
        diagnostic_focus=focus,
        diagnosis_context="",
        previous_questions=[],
    )

    assert "Selected prerequisite code: chain.rule" in prompt
    assert "hard question must genuinely require that capability" in prompt
    assert '"diagnostic_prerequisite_code": null' in prompt
