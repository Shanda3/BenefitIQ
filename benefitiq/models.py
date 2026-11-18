"""Corpus tables. Cases are not stored."""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from benefitiq.constants import EMBEDDING_DIM


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(160), index=True)
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


class Chunk(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String(240), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.source_id"))
    section_anchor: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column("text", Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    meta: Mapped[dict] = mapped_column("metadata", JSONB)


class CodeRow(Base):
    """Structured code-table row. The label is a key into labels.yaml, never a descriptor."""

    __tablename__ = "code_rows"

    code_row_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.source_id"))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.chunk_id"))
    code_system: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    label_ref: Mapped[str] = mapped_column(String(32))
