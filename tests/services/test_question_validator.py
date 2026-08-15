import pytest

from app.modules.pretests.question_validator import (
    QuestionValidationError,
    QuestionValidator,
)


def _question() -> dict[str, object]:
    return {
        "concept_code": "math.derivative",
        "difficulty": "hard",
        "question_type": "error_analysis",
        "prompt": "Seorang siswa menulis langkah solusi yang salah berikut. Evaluasi kesalahannya.",
        "explanation": "Aturan pangkat harus diterapkan pada setiap suku.",
        "expected_reasoning": "Periksa setiap langkah lalu bandingkan hasilnya.",
        "options": [
            {"label": "A", "text": "Gunakan aturan pangkat pada setiap suku.", "is_correct": True},
            {"label": "B", "text": "Pilih rumus turunan hasil kali.", "is_correct": False},
            {"label": "C", "text": "Terapkan aturan rantai dua kali.", "is_correct": False},
            {"label": "D", "text": "Langkah pertama siswa sudah cukup.", "is_correct": False},
        ],
    }


def test_solution_strategy_options_are_allowed():
    QuestionValidator().validate_question(
        concept_code="math.derivative",
        difficulty="hard",
        question=_question(),
    )


def test_duplicate_option_text_is_rejected():
    question = _question()
    question["options"][1]["text"] = question["options"][0]["text"]

    with pytest.raises(
        QuestionValidationError,
        match="option texts must be unique",
    ):
        QuestionValidator().validate_question(
            concept_code="math.derivative",
            difficulty="hard",
            question=question,
        )


def test_option_label_as_answer_text_is_rejected():
    question = _question()
    question["options"][0]["text"] = "Option A"

    with pytest.raises(
        QuestionValidationError,
        match="complete answer, not an option label",
    ):
        QuestionValidator().validate_question(
            concept_code="math.derivative",
            difficulty="hard",
            question=question,
        )


def test_exactly_one_correct_option_is_required():
    question = _question()
    question["options"][1]["is_correct"] = True
    with pytest.raises(
        QuestionValidationError,
        match="exactly 1 correct option",
    ):
        QuestionValidator().validate_question(
            concept_code="math.derivative",
            difficulty="hard",
            question=question,
        )


def test_hard_reasoning_type_does_not_depend_on_keyword_matching():
    question = _question()
    question["question_type"] = "multi_step_application"
    question["prompt"] = "Perhatikan representasi fungsi berikut dan pilih kesimpulan yang konsisten."
    QuestionValidator().validate_question(
        concept_code="math.derivative",
        difficulty="hard",
        question=question,
    )


def test_medium_prompt_is_not_rejected_by_keyword_heuristics():
    question = _question()
    question["difficulty"] = "medium"
    question["prompt"] = "Tentukan turunan pertama dari g(x)=cos(x^2)."

    QuestionValidator().validate_question(
        concept_code="math.derivative",
        difficulty="medium",
        question=question,
    )
