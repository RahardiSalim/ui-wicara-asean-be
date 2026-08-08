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
        question_type = str(question.get("question_type") or "").strip().lower()
        if question_type not in VALID_QUESTION_TYPES:
            raise QuestionValidationError("Generated question is missing or has unsupported question_type.")
        if not str(question.get("explanation", "")).strip():
            raise QuestionValidationError("Generated question is missing explanation.")
        if not str(question.get("expected_reasoning", "")).strip():
            raise QuestionValidationError("Generated question is missing expected reasoning.")

        options = question.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise QuestionValidationError("Generated question must have exactly 4 options.")
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
        if correct_count != 1:
            raise QuestionValidationError("Generated question must have exactly 1 correct option.")


def _normalize_option_text(option_text: str) -> str:
    return re.sub(r"\s+", " ", option_text.strip().lower())
