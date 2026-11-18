"""Exact code lookup and hybrid keyword plus vector search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from benefitiq.models import Chunk, CodeRow, Source
from benefitiq.retrieval.embeddings import embed, vector_literal


@dataclass
class Record:
    source_id: str
    chunk_id: str
    section_anchor: str
    metadata: dict[str, Any]
    url: str
    title: str
    publisher: str
    effective_from: date
    effective_to: date | None
    payer: str | None
    state: str | None
    program: str | None
    plan_type: str | None
    doc_type: str
    text: str


@dataclass
class Hit:
    chunk_id: str
    source_id: str
    section_anchor: str
    url: str
    title: str
    effective_from: date
    effective_to: date | None
    keyword_score: float
    vector_score: float


@dataclass
class CodeHit:
    code_row_id: str
    code_system: str
    code: str
    label_ref: str
    source_id: str
    chunk_id: str
    url: str
    title: str
    effective_from: date
    effective_to: date | None


def _in_window(dos: date):
    return (
        Source.effective_from <= dos,
        or_(Source.effective_to.is_(None), Source.effective_to >= dos),
    )


def _record(chunk: Chunk, source: Source) -> Record:
    return Record(
        source_id=source.source_id,
        chunk_id=chunk.chunk_id,
        section_anchor=chunk.section_anchor,
        metadata=dict(chunk.meta or {}),
        url=source.url,
        title=source.title,
        publisher=source.publisher,
        effective_from=source.effective_from,
        effective_to=source.effective_to,
        payer=source.payer,
        state=source.state,
        program=source.program,
        plan_type=source.plan_type,
        doc_type=source.doc_type,
        text=chunk.body,
    )


class CorpusRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_record(self, source_id: str, chunk_id: str) -> Record | None:
        row = self.session.execute(
            select(Chunk, Source)
            .join(Source, Chunk.source_id == Source.source_id)
            .where(Chunk.chunk_id == chunk_id, Chunk.source_id == source_id)
        ).one_or_none()
        if row is None:
            return None
        chunk, source = row
        if not source.url or source.effective_from is None:
            return None
        return _record(chunk, source)

    def scenario_has_records(self, scenario: str, dos: date) -> bool:
        rows = self.session.execute(
            select(Chunk.chunk_id)
            .join(Source, Chunk.source_id == Source.source_id)
            .where(Chunk.meta["scenario"].astext == scenario)
            .where(*_in_window(dos))
            .limit(1)
        ).first()
        return rows is not None

    def records(
        self,
        record_kind: str,
        *,
        scenario: str,
        dos: date,
        state: str | None,
        plan_type: str | None,
        payers: list[str],
        payer_mode: str,
    ) -> list[Record]:
        query = (
            select(Chunk, Source)
            .join(Source, Chunk.source_id == Source.source_id)
            .where(Chunk.meta["record_kind"].astext == record_kind)
            .where(Chunk.meta["scenario"].astext == scenario)
            .where(*_in_window(dos))
        )
        if state is not None:
            query = query.where(or_(Source.state.is_(None), Source.state == state))
        if plan_type is not None:
            query = query.where(or_(Source.plan_type.is_(None), Source.plan_type == plan_type))
        found: list[Record] = []
        for chunk, source in self.session.execute(query).all():
            if payer_mode == "match" and source.payer not in payers:
                continue
            if payer_mode == "match_or_general" and source.payer is not None and source.payer not in payers:
                continue
            found.append(_record(chunk, source))
        return found

    def bundle_rules(
        self,
        *,
        dos: date,
        payers: list[str],
        state: str | None,
        plan_type: str | None,
    ) -> list[Record]:
        query = (
            select(Chunk, Source)
            .join(Source, Chunk.source_id == Source.source_id)
            .where(Chunk.meta["record_kind"].astext == "bundle_rule")
            .where(*_in_window(dos))
            .where(Source.payer.in_(payers) if payers else Source.payer.is_(None))
        )
        if state is not None:
            query = query.where(or_(Source.state.is_(None), Source.state == state))
        if plan_type is not None:
            query = query.where(or_(Source.plan_type.is_(None), Source.plan_type == plan_type))
        return [_record(chunk, source) for chunk, source in self.session.execute(query).all()]

    def lookup_code(
        self,
        code: str,
        *,
        dos: date,
        state: str | None = None,
        plan_type: str | None = None,
    ) -> list[CodeHit]:
        query = (
            select(CodeRow, Source)
            .join(Source, CodeRow.source_id == Source.source_id)
            .where(CodeRow.code == code)
            .where(*_in_window(dos))
        )
        if state is not None:
            query = query.where(or_(Source.state.is_(None), Source.state == state))
        if plan_type is not None:
            query = query.where(or_(Source.plan_type.is_(None), Source.plan_type == plan_type))
        hits: list[CodeHit] = []
        for code_row, source in self.session.execute(query).all():
            hits.append(
                CodeHit(
                    code_row_id=code_row.code_row_id,
                    code_system=code_row.code_system,
                    code=code_row.code,
                    label_ref=code_row.label_ref,
                    source_id=source.source_id,
                    chunk_id=code_row.chunk_id,
                    url=source.url,
                    title=source.title,
                    effective_from=source.effective_from,
                    effective_to=source.effective_to,
                )
            )
        return hits

    def hybrid_search(
        self,
        query: str,
        *,
        dos: date,
        payer: str | None = None,
        state: str | None = None,
        plan_type: str | None = None,
        program: str | None = None,
        limit: int = 8,
    ) -> list[Hit]:
        """Keyword hits and nearest vectors, merged, with the date window applied to both."""
        keyword_hits = self._keyword_hits(
            query,
            dos=dos,
            payer=payer,
            state=state,
            plan_type=plan_type,
            program=program,
            limit=limit,
        )
        vector_hits = self._vector_hits(
            query,
            dos=dos,
            payer=payer,
            state=state,
            plan_type=plan_type,
            program=program,
            limit=limit,
        )
        merged: dict[str, Hit] = {}
        for hit in keyword_hits + vector_hits:
            current = merged.get(hit.chunk_id)
            if current is None:
                merged[hit.chunk_id] = hit
                continue
            current.keyword_score = max(current.keyword_score, hit.keyword_score)
            current.vector_score = max(current.vector_score, hit.vector_score)
        ranked = sorted(
            merged.values(),
            key=lambda hit: (0.5 * hit.keyword_score) + (0.5 * hit.vector_score),
            reverse=True,
        )
        return ranked[:limit]

    def _filter_sql(self) -> str:
        return """
            s.effective_from <= :dos
            AND (s.effective_to IS NULL OR s.effective_to >= :dos)
            AND (CAST(:payer AS text) IS NULL OR s.payer IS NULL OR s.payer = :payer)
            AND (CAST(:state AS text) IS NULL OR s.state IS NULL OR s.state = :state)
            AND (CAST(:plan_type AS text) IS NULL OR s.plan_type IS NULL OR s.plan_type = :plan_type)
            AND (CAST(:program AS text) IS NULL OR s.program IS NULL OR s.program = :program)
        """

    def _params(self, query: str, *, dos: date, payer, state, plan_type, program, limit: int) -> dict:
        return {
            "q": query,
            "embedding": vector_literal(embed(query)),
            "dos": dos,
            "payer": payer,
            "state": state,
            "plan_type": plan_type,
            "program": program,
            "limit": limit,
        }

    def _rows_to_hits(self, rows, *, keyword: bool) -> list[Hit]:
        hits: list[Hit] = []
        for row in rows:
            hits.append(
                Hit(
                    chunk_id=row.chunk_id,
                    source_id=row.source_id,
                    section_anchor=row.section_anchor,
                    url=row.url,
                    title=row.title,
                    effective_from=row.effective_from,
                    effective_to=row.effective_to,
                    keyword_score=float(row.keyword_score or 0) if keyword else 0.0,
                    vector_score=float(row.vector_score or 0) if not keyword else 0.0,
                )
            )
        return hits

    def _keyword_hits(self, query: str, **kwargs) -> list[Hit]:
        sql = text(
            f"""
            SELECT c.chunk_id, c.section_anchor, s.source_id, s.url, s.title,
                   s.effective_from, s.effective_to,
                   ts_rank(c.tsv, plainto_tsquery('english', :q)) AS keyword_score,
                   0 AS vector_score
            FROM chunks c
            JOIN sources s ON s.source_id = c.source_id
            WHERE {self._filter_sql()}
              AND c.tsv @@ plainto_tsquery('english', :q)
            ORDER BY keyword_score DESC
            LIMIT :limit
            """
        )
        rows = self.session.execute(sql, self._params(query, **kwargs)).all()
        return self._rows_to_hits(rows, keyword=True)

    def _vector_hits(self, query: str, **kwargs) -> list[Hit]:
        sql = text(
            f"""
            SELECT c.chunk_id, c.section_anchor, s.source_id, s.url, s.title,
                   s.effective_from, s.effective_to,
                   0 AS keyword_score,
                   1 - (c.embedding <=> CAST(:embedding AS vector)) AS vector_score
            FROM chunks c
            JOIN sources s ON s.source_id = c.source_id
            WHERE {self._filter_sql()}
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )
        rows = self.session.execute(sql, self._params(query, **kwargs)).all()
        return self._rows_to_hits(rows, keyword=False)
