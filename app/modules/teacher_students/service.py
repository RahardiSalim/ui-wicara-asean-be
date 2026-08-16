from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.accounts.models import UserAccount
from app.modules.teacher_students.models import TeacherStudentConnection


class ConnectionError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _display_name(account: UserAccount) -> str:
    return account.display_name.strip() or account.email or "User"


def to_connection_read(connection: TeacherStudentConnection) -> dict[str, object]:
    return {
        "id": connection.id,
        "status": connection.status,
        "teacher_id": connection.teacher_id,
        "teacher_name": _display_name(connection.teacher),
        "teacher_email": connection.teacher.email,
        "student_id": connection.student_id,
        "student_name": _display_name(connection.student),
        "student_email": connection.student.email,
        "requested_at": connection.requested_at,
        "responded_at": connection.responded_at,
    }


def _with_people():
    return (
        selectinload(TeacherStudentConnection.teacher),
        selectinload(TeacherStudentConnection.student),
    )


def invite_student(
    session: Session,
    *,
    teacher: UserAccount,
    student_email: str,
) -> TeacherStudentConnection:
    normalized_email = student_email.strip().lower()
    student = session.scalar(
        select(UserAccount).where(func.lower(UserAccount.email) == normalized_email)
    )
    if student is None or student.role != "learner":
        raise ConnectionError("No student account was found for that email.", status_code=404)
    if student.id == teacher.id:
        raise ConnectionError("You cannot invite your own account.")

    connection = session.scalar(
        select(TeacherStudentConnection).where(
            TeacherStudentConnection.teacher_id == teacher.id,
            TeacherStudentConnection.student_id == student.id,
        )
    )
    now = datetime.now(UTC)
    if connection is None:
        connection = TeacherStudentConnection(
            teacher_id=teacher.id,
            student_id=student.id,
            status="pending",
            requested_at=now,
        )
    elif connection.status == "accepted":
        raise ConnectionError("This student is already connected.", status_code=409)
    elif connection.status == "pending":
        raise ConnectionError("An invitation is already pending.", status_code=409)
    else:
        connection.status = "pending"
        connection.requested_at = now
        connection.responded_at = None

    session.add(connection)
    session.commit()
    return session.scalar(
        select(TeacherStudentConnection)
        .options(*_with_people())
        .where(TeacherStudentConnection.id == connection.id)
    )


def list_for_teacher(
    session: Session, teacher: UserAccount
) -> list[TeacherStudentConnection]:
    return list(
        session.scalars(
            select(TeacherStudentConnection)
            .options(*_with_people())
            .where(TeacherStudentConnection.teacher_id == teacher.id)
            .order_by(
                TeacherStudentConnection.status.asc(),
                TeacherStudentConnection.requested_at.desc(),
            )
        )
    )


def list_for_student(
    session: Session, student: UserAccount
) -> list[TeacherStudentConnection]:
    return list(
        session.scalars(
            select(TeacherStudentConnection)
            .options(*_with_people())
            .where(TeacherStudentConnection.student_id == student.id)
            .order_by(
                TeacherStudentConnection.status.asc(),
                TeacherStudentConnection.requested_at.desc(),
            )
        )
    )


def respond_to_invitation(
    session: Session,
    *,
    student: UserAccount,
    connection_id: uuid.UUID,
    accept: bool,
) -> TeacherStudentConnection:
    connection = session.scalar(
        select(TeacherStudentConnection).where(
            TeacherStudentConnection.id == connection_id,
            TeacherStudentConnection.student_id == student.id,
        )
    )
    if connection is None:
        raise ConnectionError("Invitation was not found.", status_code=404)
    if connection.status != "pending":
        raise ConnectionError("This invitation has already been answered.", status_code=409)

    connection.status = "accepted" if accept else "rejected"
    connection.responded_at = datetime.now(UTC)
    session.add(connection)
    session.commit()
    return session.scalar(
        select(TeacherStudentConnection)
        .options(*_with_people())
        .where(TeacherStudentConnection.id == connection.id)
    )


def disconnect(
    session: Session,
    *,
    account: UserAccount,
    connection_id: uuid.UUID,
) -> None:
    connection = session.get(TeacherStudentConnection, connection_id)
    if connection is None or account.id not in {
        connection.teacher_id,
        connection.student_id,
    }:
        raise ConnectionError("Connection was not found.", status_code=404)
    session.delete(connection)
    session.commit()


def connected_student_for_teacher(
    session: Session,
    *,
    teacher: UserAccount,
    student_id: uuid.UUID,
) -> UserAccount:
    connection = session.scalar(
        select(TeacherStudentConnection).where(
            TeacherStudentConnection.teacher_id == teacher.id,
            TeacherStudentConnection.student_id == student_id,
            TeacherStudentConnection.status == "accepted",
        )
    )
    if connection is None:
        raise ConnectionError(
            "The student has not accepted this teacher connection.", status_code=403
        )
    student = session.get(UserAccount, student_id)
    if student is None or student.role != "learner":
        raise ConnectionError("Student account was not found.", status_code=404)
    return student
