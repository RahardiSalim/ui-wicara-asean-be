from uuid import UUID

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.accounts.dependencies import get_current_account
from app.modules.accounts.models import UserAccount


TEACHER_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TEACHER_ID = UUID("10000000-0000-4000-8000-000000000002")
STUDENT_ID = UUID("20000000-0000-4000-8000-000000000001")


def _account(
    account_id: UUID,
    *,
    email: str,
    display_name: str,
    role: str,
) -> UserAccount:
    return UserAccount(
        id=account_id,
        supabase_user_id=f"supabase-{account_id}",
        email=email,
        display_name=display_name,
        role=role,
        provider_subject=f"supabase-{account_id}",
    )


def test_teacher_invite_requires_student_consent_before_progress_access(client):
    session = next(client.app.dependency_overrides[get_session]())
    session.add_all(
        [
            _account(
                TEACHER_ID,
                email="teacher@example.com",
                display_name="Teacher One",
                role="teacher",
            ),
            _account(
                OTHER_TEACHER_ID,
                email="other-teacher@example.com",
                display_name="Teacher Two",
                role="teacher",
            ),
            _account(
                STUDENT_ID,
                email="student@example.com",
                display_name="Student One",
                role="learner",
            ),
        ]
    )
    session.commit()

    actor = {"id": TEACHER_ID}

    def override_current_account(
        session: Session = Depends(get_session),
    ) -> UserAccount:
        return session.get(UserAccount, actor["id"])

    client.app.dependency_overrides[get_current_account] = override_current_account

    invited = client.post(
        "/api/v1/teacher-students/invitations",
        json={"email": " Student@Example.com "},
    )
    assert invited.status_code == 201
    invitation = invited.json()
    assert invitation["status"] == "pending"
    assert invitation["student_id"] == str(STUDENT_ID)
    assert invitation["teacher_name"] == "Teacher One"

    blocked = client.get(
        f"/api/v1/teacher-students/teacher/students/{STUDENT_ID}/progress"
    )
    assert blocked.status_code == 403

    duplicate = client.post(
        "/api/v1/teacher-students/invitations",
        json={"email": "student@example.com"},
    )
    assert duplicate.status_code == 409

    actor["id"] = STUDENT_ID
    pending = client.get("/api/v1/teacher-students/student/connections")
    assert pending.status_code == 200
    assert pending.json()["items"][0]["status"] == "pending"

    accepted = client.post(
        f"/api/v1/teacher-students/invitations/{invitation['id']}/accept"
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    actor["id"] = TEACHER_ID
    progress = client.get(
        f"/api/v1/teacher-students/teacher/students/{STUDENT_ID}/progress"
    )
    assert progress.status_code == 200
    body = progress.json()
    assert body["student_name"] == "Student One"
    assert body["overview"]["total_attempts"] == 0
    assert body["overview"]["overall_avg_mastery"] == 0
    assert body["at_risk"]["items"] == []

    actor["id"] = OTHER_TEACHER_ID
    forbidden = client.get(
        f"/api/v1/teacher-students/teacher/students/{STUDENT_ID}/progress"
    )
    assert forbidden.status_code == 403


def test_student_can_reject_and_teacher_can_resend(client):
    session = next(client.app.dependency_overrides[get_session]())
    session.add_all(
        [
            _account(
                TEACHER_ID,
                email="teacher@example.com",
                display_name="Teacher",
                role="teacher",
            ),
            _account(
                STUDENT_ID,
                email="student@example.com",
                display_name="Student",
                role="learner",
            ),
        ]
    )
    session.commit()

    actor = {"id": TEACHER_ID}

    def override_current_account(
        session: Session = Depends(get_session),
    ) -> UserAccount:
        return session.get(UserAccount, actor["id"])

    client.app.dependency_overrides[get_current_account] = override_current_account

    invited = client.post(
        "/api/v1/teacher-students/invitations",
        json={"email": "student@example.com"},
    ).json()

    actor["id"] = STUDENT_ID
    rejected = client.post(
        f"/api/v1/teacher-students/invitations/{invited['id']}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    actor["id"] = TEACHER_ID
    resent = client.post(
        "/api/v1/teacher-students/invitations",
        json={"email": "student@example.com"},
    )
    assert resent.status_code == 201
    assert resent.json()["id"] == invited["id"]
    assert resent.json()["status"] == "pending"

    actor["id"] = STUDENT_ID
    removed = client.delete(
        f"/api/v1/teacher-students/connections/{invited['id']}"
    )
    assert removed.status_code == 204
    assert client.get(
        "/api/v1/teacher-students/student/connections"
    ).json()["items"] == []
