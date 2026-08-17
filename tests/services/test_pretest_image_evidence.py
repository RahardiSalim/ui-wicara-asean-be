import json
from types import SimpleNamespace
from uuid import uuid4

from app.core.config import get_settings
from app.modules.accounts.models import UserAccount
from app.modules.ai.schemas import AIGenerationResponse
from app.modules.evidence.canvas_upload_service import CanvasUploadService
from app.modules.evidence.models import ImageAsset
from app.modules.learning.models import AssessmentOption, AssessmentQuestion
from app.modules.pretests import evidence_evaluator
from app.modules.pretests.evidence_evaluator import PretestEvidenceEvaluator
from app.modules.pretests.evidence_evaluator import _normalize_structured_method_result


def test_explicit_chain_rule_gap_is_kept_when_model_omits_auxiliary_trace_fields():
    result = _normalize_structured_method_result(
        {
            "method_valid": False,
            "primary_gap_code": "chain_rule",
            "reasoning_signal": "misconception",
            "method_reason": "The derivative only differentiates the outer sine function.",
        },
        allowed_codes={"curve_sketching", "chain_rule", "trig_derivative"},
        source="test:model",
    )

    assert result["suspected_prerequisite_code"] == "chain_rule"


def test_uploaded_evidence_image_is_persisted(db_session, tmp_path):
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"fake-png-evidence"
    user = UserAccount(
        supabase_user_id="image-evidence-user",
        email="image-evidence@example.com",
        display_name="Image Evidence",
    )
    db_session.add(user)
    db_session.commit()
    settings = get_settings().model_copy(
        update={"media_storage_local_dir": str(tmp_path)}
    )

    result = CanvasUploadService().create_uploaded_image_asset(
        db_session,
        user=user,
        content=image_bytes,
        mime_type="image/png",
        settings=settings,
    )

    asset = db_session.get(ImageAsset, result.id)
    assert asset is not None
    assert asset.mime_type == "image/png"
    assert (tmp_path / asset.storage_path).read_bytes() == image_bytes
    assert len(asset.checksum or "") == 64


def test_written_method_evaluator_sends_attached_image_to_ai(monkeypatch, tmp_path):
    image_path = tmp_path / "work.png"
    image_path.write_bytes(b"fake-image")
    asset = ImageAsset(
        id=uuid4(),
        user_id=uuid4(),
        storage_path=str(image_path),
        mime_type="image/png",
    )
    question = AssessmentQuestion(
        id=uuid4(),
        session_id=uuid4(),
        step_label="Adaptive Pretest",
        topic="Chain rule",
        prompt=r"Differentiate $\sin(x^2)$.",
        helper_text="",
        difficulty_label="Hard",
        expected_reasoning=r"$2x\cos(x^2)$",
        rubric_json={},
    )
    selected = AssessmentOption(
        id=uuid4(),
        question_id=question.id,
        option_key="A",
        label="A",
        text=r"$2x\cos(x^2)$",
        is_correct=True,
        sort_order=1,
    )
    question.options = [selected]
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        return AIGenerationResponse(
            provider="test",
            model="vision-test",
            text=json.dumps(
                {
                    "reasoning_score": 0.3,
                    "reasoning_signal": "misconception",
                    "feedback": "The inner derivative was omitted.",
                    "method_valid": False,
                    "evidence_tags": ["inner_derivative_omitted"],
                    "primary_gap_code": "chain_rule",
                    "gap_confidence": 0.94,
                    "step_results": [
                        {
                            "concept_code": "chain_rule",
                            "status": "fail",
                            "evidence": "The written derivative omits the inner factor.",
                        }
                    ],
                    "method_reason": "Visible work omits the inner derivative.",
                }
            ),
        )

    monkeypatch.setattr(
        evidence_evaluator,
        "get_ai_settings",
        lambda: SimpleNamespace(openrouter_api_key="test-key"),
    )
    monkeypatch.setattr(evidence_evaluator.ai_client, "generate", fake_generate)

    result = evidence_evaluator._evaluate_written_method_with_ai(
        question=question,
        selected_option=selected,
        typed_reasoning="",
        candidates=[{"concept_code": "chain_rule"}],
        image_asset=asset,
    )

    assert result is not None
    assert result["method_valid"] is False
    assert result["suspected_prerequisite_code"] == "chain_rule"
    assert captured["inputs"] == [
        {
            "type": "image",
            "mime_type": "image/png",
            "file_path": str(image_path),
        }
    ]
    assert "attached work image" in captured["user_instruction"]
    assert captured["params"]["reasoning"] == {"enabled": False}


def test_image_only_evidence_becomes_vision_scored_canvas(monkeypatch, tmp_path):
    image_path = tmp_path / "work.png"
    image_path.write_bytes(b"fake-image")
    asset_id = uuid4()
    asset = ImageAsset(
        id=asset_id,
        user_id=uuid4(),
        storage_path=str(image_path),
        mime_type="image/png",
    )
    question = AssessmentQuestion(
        id=uuid4(),
        session_id=uuid4(),
        step_label="Adaptive Pretest",
        topic="Chain rule",
        prompt=r"Differentiate $\sin(x^2)$.",
        helper_text="",
        difficulty_label="Hard",
        expected_reasoning=r"$2x\cos(x^2)$",
        rubric_json={},
    )
    selected = AssessmentOption(
        id=uuid4(),
        question_id=question.id,
        option_key="A",
        label="A",
        text=r"$2x\cos(x^2)$",
        is_correct=True,
        sort_order=1,
    )
    question.options = [selected]
    structured = {
        "reasoning_score": 0.3,
        "reasoning_signal": "misconception",
        "reasoning_feedback": "Inner derivative omitted.",
        "reasoning_evaluation_source": "test:vision",
        "method_valid": False,
        "evidence_tags": ["inner_derivative_omitted"],
        "suspected_prerequisite_code": "chain_rule",
        "method_reason": "Visible work omits the inner derivative.",
        "method_evaluation_source": "test:vision",
    }
    monkeypatch.setattr(
        evidence_evaluator,
        "_evaluate_written_method_with_ai",
        lambda **_: structured,
    )
    fake_session = SimpleNamespace(
        get=lambda model, identifier: asset
        if model is ImageAsset and identifier == asset_id
        else None
    )

    result = PretestEvidenceEvaluator().evaluate(
        fake_session,
        question=question,
        selected_option=selected,
        typed_reasoning="",
        canvas_asset_id=asset_id,
        graph_scope={},
    )

    assert result["canvas_status"] == "vision_evaluated"
    assert result["canvas_score"] == 0.3
    assert result["reasoning_score"] is None
    assert result["method_valid"] is False
    assert result["diagnostic_signal"] == "method_invalid_despite_correct_answer"
