"""Ingest a local manifest into the source registry.

Documents are inserted, never overwritten. A new policy version is a new
source_id with its own effective window. Re-ingesting identical bytes is a no-op.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from benefitiq.db import session_scope
import benefitiq.ingestion.policy_text  # noqa: F401  registers the policy-text loader
import benefitiq.ingestion.public_loaders  # noqa: F401  registers public loaders
from benefitiq.ingestion.loaders import (
    IngestionError,
    LoadedDocument,
    SourceSpec,
    get_loader,
)
from benefitiq.models import Chunk, CodeRow, Source
from benefitiq.retrieval.embeddings import embed_many

logger = logging.getLogger("benefitiq.ingest")


class ManifestEntry(BaseModel):
    path: str
    source_id: str
    family_id: str
    title: str
    publisher: str
    url: str
    effective_from: date
    effective_to: date | None = None
    doc_type: str
    loader: str
    payer: str | None = None
    state: str | None = None
    program: str | None = None
    plan_type: str | None = None

    @field_validator("url")
    @classmethod
    def url_must_be_present(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("url is required")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return cleaned

    @field_validator("effective_from")
    @classmethod
    def effective_from_required(cls, value: date) -> date:
        if value is None:
            raise ValueError("effective_from is required")
        return value

    @field_validator("source_id", "family_id", "title", "publisher", "doc_type", "loader")
    @classmethod
    def required_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("required text field is empty")
        return cleaned


class Manifest(BaseModel):
    documents: list[ManifestEntry] = Field(min_length=1)


def load_manifest(path: Path) -> tuple[Path, Manifest]:
    path = path.resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IngestionError(f"cannot read manifest: {path}") from exc
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise IngestionError(f"manifest rejected: {exc}") from exc
    for entry in manifest.documents:
        if entry.effective_to is not None and entry.effective_to < entry.effective_from:
            raise IngestionError(
                f"{entry.source_id} effective_to is earlier than effective_from"
            )
    return path.parent, manifest


def _content_hash(file_path: Path, entry: ManifestEntry) -> str:
    payload = file_path.read_bytes() + b"\n" + entry.model_dump_json().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ranges_overlap(
    left_from: date,
    left_to: date | None,
    right_from: date,
    right_to: date | None,
) -> bool:
    left_end = left_to or date.max
    right_end = right_to or date.max
    return left_from <= right_end and right_from <= left_end


def _warn_on_overlap(session: Session, entry: ManifestEntry) -> None:
    rows = session.scalars(select(Source).where(Source.family_id == entry.family_id)).all()
    for existing in rows:
        if existing.source_id == entry.source_id:
            continue
        if _ranges_overlap(
            existing.effective_from,
            existing.effective_to,
            entry.effective_from,
            entry.effective_to,
        ):
            logger.warning(
                "overlapping effective window family_id=%s source_id=%s other_source_id=%s",
                entry.family_id,
                entry.source_id,
                existing.source_id,
            )


def _insert_document(
    session: Session,
    entry: ManifestEntry,
    loaded: LoadedDocument,
    content_hash: str,
) -> str:
    existing = session.get(Source, entry.source_id)
    if existing is not None:
        if existing.content_hash == content_hash:
            return "skipped"
        raise IngestionError(
            f"refusing to overwrite source '{entry.source_id}'. "
            "Add a new source_id with its own effective window."
        )
    _warn_on_overlap(session, entry)
    session.add(
        Source(
            source_id=entry.source_id,
            family_id=entry.family_id,
            title=entry.title,
            publisher=entry.publisher,
            url=entry.url,
            effective_from=entry.effective_from,
            effective_to=entry.effective_to,
            retrieved_at=datetime.now(timezone.utc),
            content_hash=content_hash,
            doc_type=entry.doc_type,
            payer=entry.payer,
            state=entry.state,
            program=entry.program,
            plan_type=entry.plan_type,
        )
    )
    session.flush()
    loaded_chunks = list(loaded.chunks)
    vectors = embed_many([chunk.text for chunk in loaded_chunks])
    _flush_batches(
        session,
        (
            Chunk(
                chunk_id=chunk.chunk_id,
                source_id=entry.source_id,
                section_anchor=chunk.section_anchor,
                body=chunk.text,
                embedding=vector,
                meta=chunk.metadata,
            )
            for chunk, vector in zip(loaded_chunks, vectors, strict=True)
        ),
    )
    _flush_batches(
        session,
        (
            CodeRow(
                code_row_id=code.code_row_id,
                source_id=entry.source_id,
                chunk_id=code.chunk_id,
                code_system=code.code_system,
                code=code.code,
                label_ref=code.label_ref,
            )
            for code in loaded.code_rows
        ),
    )
    return "inserted"


def _flush_batches(session: Session, objects, *, size: int = 400) -> None:
    """Insert in batches so a full public file does not stay in the identity map."""
    batch: list = []
    for obj in objects:
        batch.append(obj)
        if len(batch) >= size:
            session.add_all(batch)
            session.flush()
            for item in batch:
                session.expunge(item)
            batch.clear()
    if batch:
        session.add_all(batch)
        session.flush()
        for item in batch:
            session.expunge(item)


def spec_from_entry(entry: ManifestEntry) -> SourceSpec:
    return SourceSpec(
        source_id=entry.source_id,
        family_id=entry.family_id,
        title=entry.title,
        publisher=entry.publisher,
        url=entry.url,
        effective_from=entry.effective_from,
        effective_to=entry.effective_to,
        doc_type=entry.doc_type,
        loader=entry.loader,
        payer=entry.payer,
        state=entry.state,
        program=entry.program,
        plan_type=entry.plan_type,
    )


def ingest_manifest(manifest_path: Path) -> dict[str, int]:
    base_dir, manifest = load_manifest(manifest_path)
    counts = {"inserted": 0, "skipped": 0}
    with session_scope() as session:
        for entry in manifest.documents:
            file_path = (base_dir / entry.path).resolve()
            if not file_path.is_file():
                raise IngestionError(f"missing document file: {entry.path}")
            try:
                file_path.relative_to(base_dir)
            except ValueError as exc:
                raise IngestionError(
                    f"document path escapes the manifest directory: {entry.path}"
                ) from exc
            loaded = get_loader(entry.loader)(file_path, spec_from_entry(entry))
            outcome = _insert_document(session, entry, loaded, _content_hash(file_path, entry))
            counts[outcome] += 1
            logger.info(
                "ingest source_id=%s outcome=%s chunks=%s rows=%s scanned=%s",
                entry.source_id,
                outcome,
                len(loaded.chunks),
                loaded.source_rows,
                loaded.scanned_rows,
            )
    return counts
