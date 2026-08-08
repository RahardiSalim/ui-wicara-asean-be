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
        "prompt": "Seorang siswa menerapkan aturan turunan berikut. Evaluasi kesalahannya.",
        "difficulty_reason": "Membutuhkan analisis kesalahan penalaran.",
        "explanation": "Aturan pangkat harus diterapkan pada setiap suku.",
        "expected_reasoning": "Periksa setiap langkah lalu bandingkan hasilnya.",
        "options": [
            {"label": "A", "text": "Gunakan aturan pangkat pada setiap suku.", "is_correct": True},
            {"label": "B", "text": "Pilih rumus turunan hasil kali.", "is_correct": False},
            {"label": "C", "text": "Terapkan aturan rantai dua kali.", "is_correct": False},
            {"label": "D", "text": "Langkah pertama siswa sudah cukup.", "is_correct": False},
        ],
        "distractor_rationales": {
            "A": "Aturan pangkat memang sesuai untuk bentuk ini.",
            "B": "Fungsi bukan hasil kali dua fungsi.",
            "C": "Tidak ada fungsi komposisi bertingkat.",
            "D": "Kesimpulan tetap harus diverifikasi.",
        },
    }


def test_solution_strategy_options_are_allowed():
    QuestionValidator().validate_question(
        concept_code="math.derivative",
        difficulty="hard",
        question=_question(),
    )


def test_distractor_rationale_cannot_admit_another_option_is_correct():
    question = _question()
    question["distractor_rationales"]["C"] = (
        "Pernyataan ini benar secara matematis, tetapi A lebih lengkap."
    )

    with pytest.raises(
        QuestionValidationError,
        match="rationale cannot admit that the option is also correct",
    ):
        QuestionValidator().validate_question(
            concept_code="math.derivative",
            difficulty="hard",
            question=question,
        )


def test_verified_final_answer_must_match_option_and_explanation():
    question = _question()
    question["final_answer"] = "Gunakan aturan pangkat pada setiap suku."
    question["explanation"] = (
        "Aturan pangkat sesuai. Gunakan aturan pangkat pada setiap suku."
    )

    QuestionValidator().validate_question(
        concept_code="math.derivative",
        difficulty="hard",
        question=question,
    )

    question["final_answer"] = "Terapkan aturan rantai dua kali."
    with pytest.raises(
        QuestionValidationError,
        match="final_answer must exactly match",
    ):
        QuestionValidator().validate_question(
            concept_code="math.derivative",
            difficulty="hard",
            question=question,
        )
