from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.learning.schemas import AnimationQueueResponse, MediaArtifactRead


class WorkspaceCreateRequest(BaseModel):
    track_id: UUID
    module_id: UUID
    content_mode: str = Field(default="chat", min_length=2, max_length=32)
    workspace_session_id: UUID | None = None
    start_new_session: bool = False


class WorkspaceEventCreateRequest(BaseModel):
    event_type: str = Field(..., min_length=2, max_length=32)
    actor_type: str = Field(default="learner", min_length=2, max_length=32)
    text_payload: str = ""
    image_asset_id: UUID | None = None
    media_artifact_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceGenerateVideoRequest(BaseModel):
    generation_mode: str = Field(default="manual", min_length=2, max_length=32)
    template_id: str | None = Field(default=None, min_length=3, max_length=120)
    spec_json: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(default="en", min_length=2, max_length=16)
    quality_profile: str = Field(default="standard", min_length=2, max_length=32)
    concept_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generation_mode", mode="before")
    @classmethod
    def normalize_generation_mode(cls, value: str) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {"auto": "context_auto", "context": "context_auto"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"manual", "context_auto"}:
            raise ValueError("generation_mode must be either 'manual' or 'context_auto'.")
        return normalized

    @model_validator(mode="after")
    def validate_payload(self) -> WorkspaceGenerateVideoRequest:
        if self.generation_mode == "manual":
            template_id = (self.template_id or "").strip()
            if not template_id:
                raise ValueError(
                    "template_id is required when generation_mode is 'manual'."
                )
        return self


class WorkspaceGenerateExplanationRequest(BaseModel):
    problem: str = Field(..., min_length=1, max_length=20000)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    max_retries: int | None = Field(default=None, ge=1, le=10)
    max_scene_concurrency: int | None = Field(default=None, ge=1, le=10)
    translate_to_chinese: bool | None = None

    @field_validator("problem")
    @classmethod
    def normalize_problem(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("problem must not be empty.")
        return text

    @field_validator("model")
    @classmethod
    def normalize_optional_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class WorkspaceEventRead(BaseModel):
    id: UUID
    workspace_id: UUID
    event_index: int
    event_type: str
    actor_type: str
    text_payload: str
    image_asset_id: UUID | None = None
    media_artifact_id: UUID | None = None
    input_event_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class WorkspaceRead(BaseModel):
    id: UUID
    track_id: UUID
    module_id: UUID
    current_topic: str
    current_topic_description: str = ""
    learner_language: str = "en"
    content_mode: str
    status: str
    events: list[WorkspaceEventRead] = Field(default_factory=list)
    last_image_asset_id: UUID | None = None
    latest_media: MediaArtifactRead | None = None
    posttest_trigger: dict[str, Any] | None = None
    current_phase: str = "engage"
    phase_transition_pending: bool = False
    posttest_eligible: bool = False


class WorkspaceSessionSummaryRead(BaseModel):
    id: UUID
    track_id: UUID
    module_id: UUID
    title: str
    preview: str
    message_count: int
    created_at: str
    updated_at: str


class WorkspaceSessionHistoryRead(BaseModel):
    sessions: list[WorkspaceSessionSummaryRead] = Field(default_factory=list)


class TutorResponseRead(BaseModel):
    text: str
    intent: str
    next_actions: list[str] = Field(default_factory=list)
    next_phase_ready: bool = False
    phase_reasoning: str | None = None


class WorkspaceMasteryUpdateRead(BaseModel):
    concept_id: UUID | None = None
    mastery_score: float | None = None
    confidence_score: float | None = None
    evidence_count: int | None = None
    status: str | None = None
    delta: float = 0.0
    reason: str


class WorkspaceEventCreateResponse(BaseModel):
    event: WorkspaceEventRead
    tutor_response: TutorResponseRead | None = None
    mastery_update: WorkspaceMasteryUpdateRead | None = None
    workspace: WorkspaceRead


class WorkspaceGenerateVideoResponse(BaseModel):
    queue: AnimationQueueResponse
    event: WorkspaceEventRead
    workspace: WorkspaceRead


class WorkspaceEduIllustrateAssetRead(BaseModel):
    kind: str
    url: str
    filename: str
    content_type: str | None = None


class WorkspaceEduIllustrateExplanationRead(BaseModel):
    success: bool = True
    text: str
    output_dir: str
    explanation_path: str | None = None
    doc_path: str | None = None
    assets: list[WorkspaceEduIllustrateAssetRead] = Field(default_factory=list)
    time_seconds: float
    model: str


class WorkspaceGenerateExplanationResponse(BaseModel):
    request_event: WorkspaceEventRead
    event: WorkspaceEventRead
    explanation: WorkspaceEduIllustrateExplanationRead
    workspace: WorkspaceRead
