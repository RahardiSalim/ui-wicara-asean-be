from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account, get_current_teacher
from app.modules.accounts.models import UserAccount
from app.modules.analytics import service as analytics_service
from app.modules.teacher_students import service
from app.modules.teacher_students.schemas import (
    ConnectionListResponse,
    ConnectionRead,
    InviteStudentRequest,
    StudentProgressResponse,
)

router = APIRouter(prefix="/teacher-students")


def _raise_connection_error(exc: service.ConnectionError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _require_student(account: UserAccount) -> None:
    if account.role != "learner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student role required.",
        )


@router.post("/invitations", response_model=ConnectionRead, status_code=201)
def invite_student(
    payload: InviteStudentRequest,
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ConnectionRead:
    try:
        connection = service.invite_student(
            session, teacher=teacher, student_email=payload.email
        )
    except service.ConnectionError as exc:
        _raise_connection_error(exc)
    return ConnectionRead(**service.to_connection_read(connection))


@router.get("/teacher/connections", response_model=ConnectionListResponse)
def teacher_connections(
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> ConnectionListResponse:
    return ConnectionListResponse(
        items=[
            ConnectionRead(**service.to_connection_read(item))
            for item in service.list_for_teacher(session, teacher)
        ]
    )


@router.get("/student/connections", response_model=ConnectionListResponse)
def student_connections(
    session: Session = Depends(get_session),
    student: UserAccount = Depends(get_current_account),
) -> ConnectionListResponse:
    _require_student(student)
    return ConnectionListResponse(
        items=[
            ConnectionRead(**service.to_connection_read(item))
            for item in service.list_for_student(session, student)
        ]
    )


@router.post("/invitations/{connection_id}/accept", response_model=ConnectionRead)
def accept_invitation(
    connection_id: uuid.UUID,
    session: Session = Depends(get_session),
    student: UserAccount = Depends(get_current_account),
) -> ConnectionRead:
    _require_student(student)
    try:
        connection = service.respond_to_invitation(
            session,
            student=student,
            connection_id=connection_id,
            accept=True,
        )
    except service.ConnectionError as exc:
        _raise_connection_error(exc)
    return ConnectionRead(**service.to_connection_read(connection))


@router.post("/invitations/{connection_id}/reject", response_model=ConnectionRead)
def reject_invitation(
    connection_id: uuid.UUID,
    session: Session = Depends(get_session),
    student: UserAccount = Depends(get_current_account),
) -> ConnectionRead:
    _require_student(student)
    try:
        connection = service.respond_to_invitation(
            session,
            student=student,
            connection_id=connection_id,
            accept=False,
        )
    except service.ConnectionError as exc:
        _raise_connection_error(exc)
    return ConnectionRead(**service.to_connection_read(connection))


@router.delete("/connections/{connection_id}", status_code=204)
def remove_connection(
    connection_id: uuid.UUID,
    session: Session = Depends(get_session),
    account: UserAccount = Depends(get_current_account),
) -> Response:
    try:
        service.disconnect(session, account=account, connection_id=connection_id)
    except service.ConnectionError as exc:
        _raise_connection_error(exc)
    return Response(status_code=204)


@router.get(
    "/teacher/students/{student_id}/progress",
    response_model=StudentProgressResponse,
)
def student_progress(
    student_id: uuid.UUID,
    session: Session = Depends(get_session),
    teacher: UserAccount = Depends(get_current_teacher),
) -> StudentProgressResponse:
    try:
        student = service.connected_student_for_teacher(
            session, teacher=teacher, student_id=student_id
        )
    except service.ConnectionError as exc:
        _raise_connection_error(exc)
    return StudentProgressResponse(
        student_id=student.id,
        student_name=student.display_name.strip() or student.email or "Student",
        student_email=student.email,
        overview=analytics_service.compute_overview(session, student),
        trends=analytics_service.compute_trends(session, student, "month"),
        velocity=analytics_service.compute_velocity(session, student),
        at_risk=analytics_service.compute_at_risk(session, student, 20),
    )
