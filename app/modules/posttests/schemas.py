from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PosttestStartRequest(BaseModel):
    workspace_session_id: UUID | None = None
    learning_goal_id: UUID | None = None
    track_id: UUID | None = None
    module_id: UUID | None = None

    @model_validator(mode="after")
    def require_target(self) -> "PosttestStartRequest":
        if (
            self.workspace_session_id is None
            and self.learning_goal_id is None
            and self.track_id is None
            and self.module_id is None
        ):
            raise ValueError("workspace_session_id, learning_goal_id, track_id, or module_id is required.")
        return self


class PosttestOptionRead(BaseModel):
    id: UUID
    label: str
    text: str


class PosttestQuestionRead(BaseModel):
    id: UUID
    concept_id: UUID | None = None
    concept_code: str
    concept_title: str
    difficulty: str
    prompt: str
    helper: str = ""
    options: list[PosttestOptionRead]
    progress: dict[str, Any] = Field(default_factory=dict)


class PosttestNodeResultRead(BaseModel):
    concept_id: UUID | None = None
    concept_code: str
    concept_title: str
    total_questions: int
    answered_count: int
    correct_count: int
    answer_percent: float = 0.0
    evidence_percent: float = 0.0
    score_percent: float = 0.0
    confidence_percent: float = 0.0
    scaled_score: float
    passed: bool
    retake_required: bool
    metric_source: str = "adaptive_posttest_evidence"


class PosttestSessionRead(BaseModel):
    session_id: UUID
    learning_goal_id: UUID | None = None
    track_id: UUID | None = None
    workspace_session_id: UUID | None = None
    posttest_source: str = ""
    language: str = "id"
    status: str
    current_question: PosttestQuestionRead | None = None
    questions: list[PosttestQuestionRead] = Field(default_factory=list)
    node_results: list[PosttestNodeResultRead]
    question_count: int
    total_questions: int


class PosttestSubmitAnswerRequest(BaseModel):
    question_id: UUID
    selected_option_id: UUID | None = None
    option_id: UUID | None = None
    confidence: int = Field(default=0, ge=0, le=10)
    typed_reasoning: str = ""
    canvas_asset_id: UUID | None = None
    used_canvas: bool = False

    @model_validator(mode="after")
    def require_selected_option(self) -> "PosttestSubmitAnswerRequest":
        if self.selected_option_id is None and self.option_id is not None:
            self.selected_option_id = self.option_id
        if self.selected_option_id is None:
            raise ValueError("selected_option_id is required.")
        return self


class PosttestEvaluationRead(BaseModel):
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


class PosttestAnswerResponse(BaseModel):
    attempt_id: UUID
    is_correct: bool
    evaluation: PosttestEvaluationRead
    node_result: PosttestNodeResultRead
    next_question: PosttestQuestionRead | None = None
    completed: bool = False


class PosttestProgressionRead(BaseModel):
    passed: bool
    track_id: UUID | None = None
    track_status: str | None = None
    track_progress_percent: int | None = None
    module_id: UUID | None = None
    module_status: str | None = None
    next_module_id: UUID | None = None
    next_module_status: str | None = None
    workspace_session_id: UUID | None = None
    workspace_status: str | None = None
    goal_status: str | None = None
    remediation_phase: str | None = None
    remediation_reason: str | None = None


class PosttestFinalizeResponse(BaseModel):
    session_id: UUID
    status: str
    node_results: list[PosttestNodeResultRead]
    retake_required_concepts: list[str]
    progression: PosttestProgressionRead | None = None
