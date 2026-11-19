"""Store downloaded payer policies as versioned, citable chunks.

A source is inserted, never overwritten. The first ingest of a file is the
current version. A later file with a different hash or a different stated date
is a new version in the needs-review state. Retrieval keeps citing the current
version until a person approves the new one.

Only catalog rows that are public, dated, and allowed to be fetched are
stored. Humana (disclaimer), licensed criteria, login-only, unverified,
not-applicable, and undated rows are skipped.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from benefitiq.db import session_scope
from benefitiq.ingestion.chunking import chunk_policy_text
from benefitiq.ingestion.loaders import IngestionError
from benefitiq.ingestion.payer_fetch import CACHE, fetch_payer_sources
from benefitiq.ingestion.policy_text import extract_text, redact_policy_text
from benefitiq.models import Chunk, Source
from benefitiq.payers.catalog import PENDING_ACCESS, PolicySource, fetchable_sources, sources
from benefitiq.payers.dates import SourceDate, parse_source_date
from benefitiq.retrieval.embeddings import embed_many

logger = logging.getLogger("benefitiq.policy_ingest")

def ledger_path() -> Path:
    override = os.environ.get("BENEFITIQ_LEDGER")
    if override:
        return Path(override)
    return CACHE.parent / "ledger.yaml"
_DATE_HINT = re.compile(r"effective|review|revised|updated", re.IGNORECASE)


@dataclass
class IngestTotals:
    inserted: int = 0
    unchanged: int = 0
    needs_review: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    failures: list[tuple[str, str]] | None = None

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "unchanged": self.unchanged,
            "needs_review": self.needs_review,
            "skipped": self.skipped,
            "failed": self.failed,
            "chunks": self.chunks,
        }


def eligible(source: PolicySource) -> bool:
    """True when this registry row may be downloaded and stored."""
    if source.access != "public":
        return False
    if source.access == PENDING_ACCESS:
        return False
    if source.date.value is None:
        return False
    return bool(source.fetch)


def content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stated_date(text: str) -> SourceDate | None:
    """Read a date the document itself states. Do not invent one."""
    sample = (text or "")[:8000]
    for line in sample.splitlines():
        if not _DATE_HINT.search(line):
            continue
        parsed = parse_source_date(line.strip())
        if parsed.value is not None:
            return parsed
    parsed = parse_source_date(sample)
    if parsed.value is not None:
        return parsed
    return None


def _version_id(catalog_id: str, version: int) -> str:
    suffix = f"-v{version}"
    keep = 200 - len(suffix)
    return f"{catalog_id[:keep]}{suffix}"


def _csv(values: tuple[str, ...] | list[str]) -> str | None:
    cleaned = [item for item in values if item]
    if not cleaned:
        return None
    return ",".join(cleaned)


def _family_rows(session: Session, catalog_id: str) -> list[Source]:
    return list(
        session.scalars(
            select(Source).where(
                Source.family_id == catalog_id,
                Source.doc_type == "policy_text",
            )
        ).all()
    )


def ingest_policy_bytes(
    session: Session,
    source: PolicySource,
    payload: bytes,
    *,
    make_current: bool,
    retrieved_at: datetime | None = None,
) -> tuple[str, int]:
    """Insert a version of one policy. Returns (outcome, chunk count).

    `make_current` is true for the deliberate first load. A yearly check passes
    false so a changed file waits for review.
    """
    if not eligible(source):
        raise IngestionError(f"{source.source_id} is not eligible to ingest")
    if source.date.value is None:
        raise IngestionError(f"{source.source_id} has no date")
    digest = content_hash(payload)
    when = retrieved_at or datetime.now(timezone.utc)
    try:
        raw_text = extract_text(payload)
    except Exception as exc:  # noqa: BLE001 - keep going; the message stays short
        raise IngestionError(f"could not read {source.source_id}") from exc
    redacted = redact_policy_text(raw_text)
    parsed = stated_date(redacted)
    rows = _family_rows(session, source.source_id)
    same = [row for row in rows if row.content_hash == digest]
    if same and (parsed is None or all(row.stated_on == parsed.value for row in same)):
        for row in same:
            row.last_checked_at = when
            row.check_state = "ok"
        return "unchanged", 0

    version = max((row.version for row in rows), default=0) + 1
    has_current = any(row.status == "current" for row in rows)
    if make_current and not has_current:
        status = "current"
        outcome = "inserted"
    else:
        status = "needs_review"
        outcome = "needs_review"
    source_id = _version_id(source.source_id, version)
    segments = _csv([link.segment_id for link in source.links])
    topic_text = _csv(list(source.topics))
    stored_date = source.date.value
    meta = {
        "catalog_id": source.source_id,
        "payer": source.payer_id,
        "segment": segments or "",
        "document_number": source.number,
        "effective_or_review_date": stored_date.isoformat(),
        "date_basis": source.date.basis,
        "url": source.url,
        "content_hash": digest,
        "retrieved_at": when.isoformat(),
        "version": version,
        "topics": topic_text or "",
    }
    chunks = chunk_policy_text(source_id, redacted, meta)
    vectors = embed_many([chunk.text for chunk in chunks])
    session.add(
        Source(
            source_id=source_id,
            family_id=source.source_id,
            title=source.title[:400],
            publisher=source.publisher[:200],
            url=source.url[:800],
            effective_from=stored_date,
            effective_to=None,
            retrieved_at=when,
            content_hash=digest,
            doc_type="policy_text",
            payer=source.payer_id,
            state=source.state,
            program=source.program,
            plan_type=source.plan_type,
            version=version,
            status=status,
            check_state="ok",
            document_number=(source.number or None),
            segment=segments,
            topics=topic_text,
            date_basis=source.date.basis,
            stated_on=parsed.value if parsed else None,
            last_checked_at=when,
        )
    )
    session.flush()
    for chunk, vector in zip(chunks, vectors, strict=True):
        session.add(
            Chunk(
                chunk_id=chunk.chunk_id,
                source_id=source_id,
                section_anchor=chunk.section_anchor,
                body=chunk.text,
                embedding=vector,
                meta=chunk.metadata,
            )
        )
    session.flush()
    logger.info(
        "policy_ingest source_id=%s outcome=%s version=%s chunks=%s",
        source.source_id,
        outcome,
        version,
        len(chunks),
    )
    return outcome, len(chunks)


def approve_policy(catalog_id: str, *, session: Session | None = None) -> str:
    """Make the newest waiting version the one retrieval cites."""

    def _approve(active: Session) -> str:
        rows = _family_rows(active, catalog_id)
        waiting = [row for row in rows if row.status == "needs_review"]
        if not waiting:
            raise IngestionError(f"no version is waiting for review for {catalog_id}")
        chosen = max(waiting, key=lambda row: row.version)
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.source_id == chosen.source_id:
                continue
            if row.status in {"current", "needs_review"}:
                row.status = "superseded"
        chosen.status = "current"
        chosen.check_state = "ok"
        chosen.last_checked_at = now
        logger.info("policy_approve source_id=%s version=%s", catalog_id, chosen.version)
        return chosen.source_id

    if session is not None:
        approved = _approve(session)
        write_ledger(session)
        return approved
    with session_scope() as active:
        approved = _approve(active)
        write_ledger(active)
        return approved


def mark_check_state(session: Session, catalog_id: str, check_state: str) -> None:
    now = datetime.now(timezone.utc)
    for row in _family_rows(session, catalog_id):
        if row.status != "current":
            continue
        row.check_state = check_state
        row.last_checked_at = now


def touch_checked(session: Session, catalog_id: str) -> None:
    mark_check_state(session, catalog_id, "ok")


def write_ledger(session: Session) -> None:
    """Write hashes and dates only. Policy text stays out of this file."""
    rows = list(
        session.scalars(select(Source).where(Source.doc_type == "policy_text")).all()
    )
    grouped: dict[str, list[Source]] = {}
    for row in rows:
        grouped.setdefault(row.family_id, []).append(row)
    documents: dict[str, dict] = {}
    for catalog_id, family in sorted(grouped.items()):
        current = next((row for row in family if row.status == "current"), None)
        waiting = [row for row in family if row.status == "needs_review"]
        chosen = current or max(family, key=lambda row: row.version)
        pending = None
        if waiting:
            newest = max(waiting, key=lambda row: row.version)
            pending = {
                "version": newest.version,
                "status": newest.status,
                "content_hash": newest.content_hash,
                "effective_from": newest.effective_from.isoformat(),
                "stated_on": newest.stated_on.isoformat() if newest.stated_on else None,
                "source_id": newest.source_id,
            }
        documents[catalog_id] = {
            "version": chosen.version if current is None else current.version,
            "status": chosen.status if current is None else current.status,
            "check_state": chosen.check_state if current is None else current.check_state,
            "content_hash": chosen.content_hash if current is None else current.content_hash,
            "effective_from": (current or chosen).effective_from.isoformat(),
            "stated_on": (current or chosen).stated_on.isoformat()
            if (current or chosen).stated_on
            else None,
            "date_basis": (current or chosen).date_basis,
            "source_id": (current or chosen).source_id,
            "url": (current or chosen).url,
            "payer": (current or chosen).payer,
            "segment": (current or chosen).segment,
            "topics": (current or chosen).topics,
            "number": (current or chosen).document_number,
            "retrieved_at": (current or chosen).retrieved_at.isoformat(),
            "last_checked_at": (current or chosen).last_checked_at.isoformat()
            if (current or chosen).last_checked_at
            else None,
            "pending": pending,
        }
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"sources": documents}, sort_keys=False),
        encoding="utf-8",
    )


def load_ledger() -> dict[str, dict]:
    path = ledger_path()
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(raw.get("sources") or {})


def ingest_policy_library(catalog: list[PolicySource] | None = None) -> IngestTotals:
    """Download eligible policies and store the first version as current."""
    totals = IngestTotals(failures=[])
    chosen = list(catalog) if catalog is not None else list(fetchable_sources())
    allowed = [item for item in chosen if eligible(item)]
    totals.skipped = len(chosen) - len(allowed)
    if catalog is None:
        fetch_outcomes = fetch_payer_sources()
    else:
        fetch_outcomes = {}
    with session_scope() as session:
        for source in allowed:
            dest = CACHE / f"{source.source_id}.bin"
            if catalog is not None and not dest.is_file():
                totals.failed += 1
                totals.failures.append((source.source_id, "missing file"))
                continue
            if catalog is None and not dest.is_file():
                totals.failed += 1
                reason = fetch_outcomes.get(source.source_id, "not downloaded")
                totals.failures.append((source.source_id, _short(reason)))
                continue
            try:
                outcome, count = ingest_policy_bytes(
                    session,
                    source,
                    dest.read_bytes(),
                    make_current=True,
                )
                session.commit()
                session.expunge_all()
            except Exception as exc:  # noqa: BLE001 - one file must not discard the others
                session.rollback()
                totals.failed += 1
                totals.failures.append((source.source_id, _short(exc.__class__.__name__)))
                logger.info("policy_ingest source_id=%s outcome=failed", source.source_id)
                continue
            totals.chunks += count
            if outcome == "inserted":
                totals.inserted += 1
            elif outcome == "needs_review":
                totals.needs_review += 1
            else:
                totals.unchanged += 1
        write_ledger(session)
    return totals


def _short(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:180]


def skipped_catalog() -> list[PolicySource]:
    """Registry rows the ingest must not download."""
    return [item for item in sources() if not eligible(item)]
