"""add consent-based teacher student connections

Revision ID: 20260810_0019_teacher_students
Revises: 20260620_0018_concept_i18n
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0019_teacher_students"
down_revision: str | Sequence[str] | None = "20260620_0018_concept_i18n"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teacher_student_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["user_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["user_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "teacher_id",
            "student_id",
            name="uq_teacher_student_connections_pair",
        ),
    )
    op.create_index(
        "ix_teacher_student_connections_teacher_id",
        "teacher_student_connections",
        ["teacher_id"],
    )
    op.create_index(
        "ix_teacher_student_connections_student_id",
        "teacher_student_connections",
        ["student_id"],
    )
    op.create_index(
        "ix_teacher_student_connections_status",
        "teacher_student_connections",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_student_connections_status",
        table_name="teacher_student_connections",
    )
    op.drop_index(
        "ix_teacher_student_connections_student_id",
        table_name="teacher_student_connections",
    )
    op.drop_index(
        "ix_teacher_student_connections_teacher_id",
        table_name="teacher_student_connections",
    )
    op.drop_table("teacher_student_connections")
