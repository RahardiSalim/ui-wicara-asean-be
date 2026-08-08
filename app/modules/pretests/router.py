from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount
from app.modules.learning import service as legacy_learning_service
from app.modules.learning.models import AssessmentSession
from app.modules.pretests.adaptive_service import (
    AdaptivePretestService,
    DuplicateQuestionAttempt,
)
from app.modules.pretests.generation_service import AssessmentQuestionGenerationError
from app.modules.pretests.schemas import PretestStartRequest, PretestSubmitAnswerRequest

router = APIRouter()
service = AdaptivePretestService()


@router.post("/pretests/start")
def start_pretest(
    payload: PretestStartRequest,
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
):
    try:
        result = service.start(
            session,
            user=user,
            learning_goal_id=payload.learning_goal_id,
            depth=payload.depth,
            max_questions=payload.max_questions,
            max_nodes_visited=payload.max_nodes_visited,
        )
    except AssessmentQuestionGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning goal not found.")
    return result


@router.get("/pretests/{session_id}")
def read_pretest(
    session_id: UUID,
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
):
    result = service.read(session, user=user, session_id=session_id)
    if result is None:
        legacy = legacy_learning_service.get_pretest_for_goal(
            session,
            user=user,
            learning_goal_id=session_id,
        )
        if legacy is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pretest session not found.")
        return legacy
    return result


@router.post("/pretests/{session_id}/answers")
def submit_pretest_answer(
    session_id: UUID,
    payload: PretestSubmitAnswerRequest,
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
):
    assessment = session.get(AssessmentSession, session_id)
    adaptive_generations = {"lazy_node_pack", "fresh_ai_questions"}
    if assessment is not None and (assessment.metadata_json or {}).get("generation") not in adaptive_generations:
        return legacy_learning_service.submit_answer_response(
            session,
            user=user,
            assessment_session_id=session_id,
            question_id=str(payload.question_id),
            option_id=str(payload.selected_option_id),
            confidence=payload.confidence,
        )
    try:
        result = service.submit_answer(
            session,
            user=user,
            session_id=session_id,
            question_id=payload.question_id,
            selected_option_id=payload.selected_option_id,
            typed_reasoning=payload.typed_reasoning,
            canvas_asset_id=payload.canvas_asset_id,
            used_canvas=payload.used_canvas,
        )
    except AssessmentQuestionGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DuplicateQuestionAttempt as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "QUESTION_ALREADY_ANSWERED", "message": "This question already has an answer."},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pretest session not found.")
    return result


@router.post("/pretests/{session_id}/finalize")
def finalize_pretest(
    session_id: UUID,
    session: Session = Depends(get_session),
    user: UserAccount = Depends(get_current_account),
):
    result = service.finalize(session, user=user, session_id=session_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pretest session not found.")
    return result
