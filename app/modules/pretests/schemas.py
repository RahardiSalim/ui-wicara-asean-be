from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PretestOptionRead(BaseModel):
    id: UUID
    label: str
    text: str


class PretestQuestionRead(BaseModel):
    id: UUID
    pack_id: UUID | None = None
    concept_code: str
    concept_title: str
    difficulty: str
    prompt: str
    helper: str = ""
    options: list[PretestOptionRead]
    progress: dict[str, Any] = Field(default_factory=dict)


class PretestStartRequest(BaseModel):
    learning_goal_id: UUID
    depth: int = Field(default=2, ge=0, le=5)
    max_questions: int = Field(default=10, ge=2, le=10)
    max_nodes_visited: int = Field(default=5, ge=1, le=20)


class PretestSessionRead(BaseModel):
    session_id: UUID
    learning_goal_id: UUID
    status: str
    target_concept: dict[str, Any]
    graph_scope: dict[str, Any]
    decision_state: dict[str, Any]
    current_question: PretestQuestionRead | None = None
    question_count: int
    max_questions: int


class PretestSubmitAnswerRequest(BaseModel):
    question_id: UUID
    selected_option_id: UUID | None = None
    option_id: UUID | None = None
    confidence: int = Field(default=0, ge=0, le=10)
    typed_reasoning: str = ""
    canvas_asset_id: UUID | None = None
    used_canvas: bool = False
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def require_selected_option(self) -> "PretestSubmitAnswerRequest":
        if self.selected_option_id is None and self.option_id is not None:
            self.selected_option_id = self.option_id
        if self.selected_option_id is None:
            raise ValueError("selected_option_id is required.")
        return self


class PretestEvaluationRead(BaseModel):
    is_correct: bool
    answer_score: float
    reasoning_score: float | None = None
    reasoning_signal: str | None = None
    reasoning_feedback: str | None = None
    reasoning_evaluation_source: str | None = None
    canvas_score: float | None = None
    evidence_score: float
    confidence: float
    diagnostic_signal: str
    canvas_status: str | None = None
    method_valid: bool | None = None
    evidence_tags: list[str] = Field(default_factory=list)
    suspected_prerequisite_code: str | None = None
    method_reason: str = ""
    method_evaluation_source: str = ""


class PretestAnswerResponse(BaseModel):
    attempt_id: UUID
    evaluation: PretestEvaluationRead
    next_action: dict[str, Any]
    next_question: PretestQuestionRead | None = None
    diagnosis: dict[str, Any] | None = None


class PretestFinalizeResponse(BaseModel):
    session_id: UUID
    status: str
    diagnosis: dict[str, Any]
    path_options: list[str]
