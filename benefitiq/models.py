"""Corpus tables. Cases are not stored."""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from benefitiq.constants import EMBEDDING_DIM


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(400))
    publisher: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(800))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    doc_type: Mapped[str] = mapped_column(String(64))
    payer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    program: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # current: cited by retrieval. needs_review: waiting for a person.
    # superseded: kept for history. blocked is recorded on check_state.
    status: Mapped[str] = mapped_column(String(32), default="current", server_default="current")
    check_state: Mapped[str] = mapped_column(String(32), default="ok", server_default="ok")
    document_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    segment: Mapped[str | None] = mapped_column(String(400), nullable=True)
    topics: Mapped[str | None] = mapped_column(String(400), nullable=True)
    date_basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), ForeignKey("sources.source_id"))
    section_anchor: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column("text", Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    meta: Mapped[dict] = mapped_column("metadata", JSONB)


class CodeRow(Base):
    """Structured code-table row. The label is a key into labels.yaml, never a descriptor."""

    __tablename__ = "code_rows"

    code_row_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), ForeignKey("sources.source_id"))
    chunk_id: Mapped[str] = mapped_column(String(320), ForeignKey("chunks.chunk_id"))
    code_system: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    label_ref: Mapped[str] = mapped_column(String(32))
