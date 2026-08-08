from __future__ import annotations

import re
from typing import Any

VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_QUESTION_TYPES = {
    "multiple_choice",
    "direct_computation",
    "concept_recognition",
    "word_problem",
    "equation_representation",
    "error_analysis",
    "missing_value",
    "strategy_comparison",
    "table_interpretation",
    "multi_step_application",
    "concept_application",
}
HARD_QUESTION_TYPES = {
    "word_problem",
    "error_analysis",
    "missing_value",
    "strategy_comparison",
    "table_interpretation",
    "multi_step_application",
}


class QuestionValidationError(ValueError):
    pass


class QuestionValidator:
    def validate_pack(
        self,
        *,
        concept_code: str,
        pack: dict[str, dict[str, Any]],
    ) -> None:
        missing = VALID_DIFFICULTIES - set(pack)
        if missing:
            raise QuestionValidationError(f"Question pack is missing: {', '.join(sorted(missing))}")
        for difficulty, question in pack.items():
            self.validate_question(concept_code=concept_code, difficulty=difficulty, question=question)

    def validate_question(
        self,
        *,
        concept_code: str,
        difficulty: str,
        question: dict[str, Any],
    ) -> None:
        if difficulty not in VALID_DIFFICULTIES:
            raise QuestionValidationError("Difficulty must be easy, medium, or hard.")
        if question.get("concept_code") != concept_code:
            raise QuestionValidationError("Generated question concept does not match requested concept.")
        if question.get("difficulty") != difficulty:
            raise QuestionValidationError("Generated question difficulty does not match requested difficulty.")
        prompt = str(question.get("prompt", "")).strip()
        if not prompt:
            raise QuestionValidationError("Generated question is missing prompt.")
        if _looks_like_vague_theory_check(prompt):
            raise QuestionValidationError("Generated question must be a concrete problem, not a vague theory check.")
        question_type = str(question.get("question_type") or "").strip().lower()
        if question_type not in VALID_QUESTION_TYPES:
            raise QuestionValidationError("Generated question is missing or has unsupported question_type.")
        difficulty_reason = str(question.get("difficulty_reason") or "").strip()
        if not difficulty_reason:
            raise QuestionValidationError("Generated question is missing difficulty_reason.")
        if not str(question.get("explanation", "")).strip():
            raise QuestionValidationError("Generated question is missing explanation.")
        if not str(question.get("expected_reasoning", "")).strip():
            raise QuestionValidationError("Generated question is missing expected reasoning.")
        if difficulty in {"medium", "hard"} and _looks_like_direct_computation_only(
            prompt,
            question_type=question_type,
        ):
            raise QuestionValidationError("Medium/hard questions must not be direct computation only.")
        if difficulty == "hard" and question_type not in HARD_QUESTION_TYPES:
            raise QuestionValidationError("Hard questions must require deeper reasoning, not basic recognition.")

        options = question.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise QuestionValidationError("Generated question must have exactly 4 options.")
        rationales = question.get("distractor_rationales")
        if not isinstance(rationales, dict):
            raise QuestionValidationError("Generated question is missing distractor_rationales.")
        correct_count = 0
        labels: set[str] = set()
        option_texts: set[str] = set()
        for option in options:
            if not isinstance(option, dict):
                raise QuestionValidationError("Generated options must be objects.")
            label = str(option.get("label", "")).strip()
            if not label:
                raise QuestionValidationError("Generated option is missing label.")
            if label in labels:
                raise QuestionValidationError("Generated option labels must be unique.")
            labels.add(label)
            if not str(option.get("text", "")).strip():
                raise QuestionValidationError("Generated option is missing text.")
            option_text = str(option.get("text", "")).strip()
            normalized_option_text = _normalize_option_text(option_text)
            if normalized_option_text in option_texts:
                raise QuestionValidationError("Generated option texts must be unique.")
            option_texts.add(normalized_option_text)
            if option.get("is_correct") is True:
                correct_count += 1
            rationale = str(rationales.get(label) or "").strip()
            if not rationale:
                raise QuestionValidationError("Generated distractor_rationales must cover every option label.")
            if option.get("is_correct") is not True and _rationale_admits_option_is_correct(
                rationale
            ):
                raise QuestionValidationError(
                    "Every distractor must be false; its rationale cannot admit that the option is also correct."
                )
        if correct_count != 1:
            raise QuestionValidationError("Generated question must have exactly 1 correct option.")
        final_answer = str(question.get("final_answer") or "").strip()
        if final_answer:
            correct_option_text = next(
                str(option.get("text") or "").strip()
                for option in options
                if option.get("is_correct") is True
            )
            if _normalize_option_text(final_answer) != _normalize_option_text(
                correct_option_text
            ):
                raise QuestionValidationError(
                    "Generated final_answer must exactly match the correct option text."
                )


def _looks_like_vague_theory_check(prompt: str) -> bool:
    normalized = prompt.lower()
    banned_fragments = (
        "apa ide utama",
        "what is the main idea",
        "what is the definition",
        "apa definisi",
        "pilih definisi",
        "choose the definition",
        "explain the concept",
        "jelaskan konsep",
    )
    return any(fragment in normalized for fragment in banned_fragments)


def _rationale_admits_option_is_correct(rationale: str) -> bool:
    normalized = " ".join(rationale.lower().split())
    admitted_correctness = (
        "pernyataan ini benar",
        "opsi ini benar",
        "jawaban ini benar",
        "benar secara matematis",
        "juga benar",
        "this statement is correct",
        "this option is correct",
        "this answer is correct",
        "mathematically correct",
        "also correct",
    )
    return any(fragment in normalized for fragment in admitted_correctness)


def _normalize_option_text(option_text: str) -> str:
    return re.sub(r"\s+", " ", option_text.strip().lower())


def _looks_like_direct_computation_only(prompt: str, *, question_type: str) -> bool:
    if question_type == "direct_computation":
        return True
    normalized = " ".join(prompt.lower().split())
    direct_markers = (
        "what is",
        "calculate",
        "compute",
        "hitung",
        "berapa hasil",
        "tentukan hasil",
    )
    direct_derivative_markers = (
        "tentukan turunan",
        "turunan pertama dari",
        "find the derivative",
        "differentiate",
    )
    has_direct_marker = any(marker in normalized for marker in direct_markers)
    has_operation = bool(re.search(r"\d+\s*(?:\\times|x|×|\*|\+|-|:|/|\\div)\s*\d+", normalized))
    contextual_terms = (
        "rani",
        "budi",
        "siti",
        "andi",
        "box",
        "bag",
        "row",
        "table",
        "student",
        "teacher",
        "kotak",
        "kantong",
        "baris",
        "murid",
        "siswa",
        "guru",
        "cerita",
        "which equation",
        "persamaan",
        "mistake",
        "kesalahan",
    )
    has_context = any(term in normalized for term in contextual_terms)
    if any(marker in normalized for marker in direct_derivative_markers):
        return not has_context
    return has_direct_marker and has_operation and not has_context
