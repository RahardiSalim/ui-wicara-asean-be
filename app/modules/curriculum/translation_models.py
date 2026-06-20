from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class ConceptTranslation(Base):
    """A localized title/description for a knowledge concept.

    Generalizes the original bilingual `id_desc`/`en_desc` columns to any
    supported language. Rows are populated on demand by the translation service
    (`source='ai'`) or seeded/curated (`source='curated'`); `id_desc`/`en_desc`
    remain the fallback for `id`/`en`.
    """

    __tablename__ = "concept_translations"
    __table_args__ = (
        UniqueConstraint("concept_id", "lang", name="uq_concept_translations_concept_lang"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_concepts.id", ondelete="CASCADE"),
        nullable=False,
    )
    lang: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="ai")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
