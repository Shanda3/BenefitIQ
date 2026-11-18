"""Citation validator.

Every published line is rebuilt from the source registry. A drafter can supply
a source id and a chunk id, but the URL, effective dates, frequency token, and
display text come from the database. Lines that fail this check become
'not found'. Lines the documentation does not support become documentation gaps.
"""

from __future__ import annotations

from datetime import date

from benefitiq.engine.draft import Draft, DraftGap, DraftGuard, DraftLine
from benefitiq.engine.evidence import Evidence
from benefitiq.labels import label_for
from benefitiq.retrieval.search import CorpusRepo, Record
from benefitiq.schemas import (
    CaseInput,
    CitationOut,
    DocumentationGap,
    Guardrail,
    Recommendation,
    ResultLine,
)


def _not_found(line: DraftLine) -> ResultLine:
    pathway = line.pathway if line.pathway in {"dental", "medical"} else "dental"
    return ResultLine(
        pathway=pathway,  # type: ignore[arg-type]
        kind=line.kind if line.kind in {"coding", "coverage", "requirement", "routing"} else "coding",  # type: ignore[arg-type]
        status="not_found",
        code_system=line.code_system,
        code=line.code,
        label=label_for(line.code),
        text="not found",
    )


def _citation(record: Record) -> CitationOut:
    return CitationOut(
        source_id=record.source_id,
        chunk_id=record.chunk_id,
        url=record.url,
        title=record.title,
        effective_from=record.effective_from,
        effective_to=record.effective_to,
        section_anchor=record.section_anchor,
    )


def _in_window(record: Record, dos: date) -> bool:
    if not record.url or record.effective_from is None:
        return False
    if record.effective_from > dos:
        return False
    if record.effective_to is not None and record.effective_to < dos:
        return False
    return True


def _load(repo: CorpusRepo, line: DraftLine, dos: date) -> Record | None:
    if not line.source_id or not line.chunk_id:
        return None
    record = repo.get_record(line.source_id, line.chunk_id)
    if record is None or not _in_window(record, dos):
        return None
    return record


def _case_span_ids(case: CaseInput) -> set[str]:
    return {span.id for span in case.spans}


def _validate_line(
    case: CaseInput,
    evidence: Evidence,
    line: DraftLine,
    repo: CorpusRepo,
    gaps: list[DraftGap],
) -> ResultLine | None:
    record = _load(repo, line, case.date_of_service)
    if record is None:
        return _not_found(line)

    kind = str(record.metadata.get("record_kind"))
    known_spans = _case_span_ids(case)

    if line.kind == "routing":
        if kind != "routing_rule":
            return _not_found(line)
        statement = record.metadata.get("statement")
        pathway = str(record.metadata.get("primary_pathway") or "")
        requires = [str(item) for item in record.metadata.get("requires") or []]
        if pathway not in {"dental", "medical"} or not isinstance(statement, str):
            return _not_found(line)
        if not set(requires).issubset(evidence.elements):
            for element in requires:
                if element not in evidence.elements:
                    gaps.append(DraftGap(element=element))
            return _not_found(line)
        spans = [span_id for span_id in evidence.spans_for(requires) if span_id in known_spans]
        return ResultLine(
            pathway=pathway,  # type: ignore[arg-type]
            kind="routing",
            status="cited",
            supporting_span_ids=spans,
            citations=[_citation(record)],
            text=statement,
        )

    if line.kind == "requirement":
        if kind != "checklist":
            return _not_found(line)
        element = str(record.metadata.get("element") or "")
        pathway = str(record.metadata.get("pathway") or line.pathway)
        if not element or pathway not in {"dental", "medical"}:
            return _not_found(line)
        status = "met" if element in evidence.elements else "missing"
        if status == "missing":
            gaps.append(DraftGap(element=element))
        spans = [span_id for span_id in evidence.spans_for([element]) if span_id in known_spans]
        return ResultLine(
            pathway=pathway,  # type: ignore[arg-type]
            kind="requirement",
            status="cited",
            supporting_span_ids=spans,
            citations=[_citation(record)],
            text=f"{element} {status}",
            requirement_element=element,
            requirement_status=status,  # type: ignore[arg-type]
        )

    if line.kind not in {"coding", "coverage"}:
        return _not_found(line)
    expected_kind = "coding_rule" if line.kind == "coding" else "coverage_rule"
    if kind != expected_kind:
        return _not_found(line)
    code = str(record.metadata.get("code") or "")
    system = str(record.metadata.get("code_system") or "")
    if code != line.code or system != line.code_system:
        return _not_found(line)
    if code not in record.text:
        return _not_found(line)
    requires = [str(item) for item in record.metadata.get("requires") or []]
    if not set(requires).issubset(evidence.elements):
        for element in requires:
            if element not in evidence.elements:
                gaps.append(DraftGap(element=element, related_code=code))
        return None
    spans = [span_id for span_id in evidence.spans_for(requires) if span_id in known_spans]
    if not spans:
        for element in requires:
            gaps.append(DraftGap(element=element, related_code=code))
        return None
    pathway = str(record.metadata.get("pathway") or line.pathway)
    if pathway not in {"dental", "medical"}:
        return _not_found(line)
    frequency = None
    if line.kind == "coverage":
        token = record.metadata.get("frequency_token")
        frequency = token if isinstance(token, str) else None
    return ResultLine(
        pathway=pathway,  # type: ignore[arg-type]
        kind=line.kind,  # type: ignore[arg-type]
        status="cited",
        code_system=system,
        code=code,
        label=label_for(code),
        supporting_span_ids=spans,
        citations=[_citation(record)],
        text=f"{system} {code}",
        frequency_token=frequency,
    )


def _validate_guard(case: CaseInput, guard: DraftGuard, repo: CorpusRepo) -> tuple[Guardrail | None, list[DraftLine]]:
    """Return the guardrail and any not-found lines that replace an unverified bundle flag."""
    citations: list[CitationOut] = []
    if guard.source_id and guard.chunk_id:
        record = repo.get_record(guard.source_id, guard.chunk_id)
        if record is not None and _in_window(record, case.date_of_service):
            kind = str(record.metadata.get("record_kind"))
            if guard.type == "unbundling" and kind == "bundle_rule":
                rule_codes = {str(code) for code in record.metadata.get("codes") or []}
                if set(guard.codes).issubset(rule_codes) and record.url:
                    citations.append(_citation(record))
            elif guard.type == "upcoding" and kind == "coding_rule":
                if record.metadata.get("code") in guard.codes:
                    citations.append(_citation(record))
            elif guard.type == "malignancy" and kind == "coding_rule":
                if record.metadata.get("code") in guard.codes:
                    citations.append(_citation(record))

    if guard.type == "unbundling" and not citations:
        replacements = [
            DraftLine(pathway="dental", kind="coverage", code_system="CDT", code=code)
            for code in guard.codes
        ]
        return None, replacements

    return (
        Guardrail(
            type=guard.type,  # type: ignore[arg-type]
            status=guard.status,  # type: ignore[arg-type]
            codes=list(guard.codes),
            message=guard.message,
            missing_elements=list(guard.missing_elements),
            citations=citations,
        ),
        [],
    )


def _merge_gaps(gaps: list[DraftGap]) -> list[DocumentationGap]:
    grouped: dict[str, list[str]] = {}
    for gap in gaps:
        if not gap.element:
            continue
        bucket = grouped.setdefault(gap.element, [])
        if gap.related_code and gap.related_code not in bucket:
            bucket.append(gap.related_code)
    return [
        DocumentationGap(element=element, related_codes=sorted(codes))
        for element, codes in sorted(grouped.items())
    ]


def finalize(
    case: CaseInput,
    evidence: Evidence,
    draft: Draft,
    repo: CorpusRepo,
) -> Recommendation:
    gaps = list(draft.gaps)
    lines: list[ResultLine] = []
    for line in draft.lines:
        validated = _validate_line(case, evidence, line, repo, gaps)
        if validated is not None:
            lines.append(validated)

    guardrails: list[Guardrail] = []
    for guard in draft.guards:
        kept, replacements = _validate_guard(case, guard, repo)
        if kept is not None:
            guardrails.append(kept)
        for replacement in replacements:
            lines.append(_not_found(replacement))

    routing_lines = [line for line in lines if line.kind == "routing"]
    cited_routing = [line for line in routing_lines if line.status == "cited"]
    if cited_routing:
        routing = cited_routing[0]
    elif routing_lines:
        routing = routing_lines[0]
    else:
        routing = _not_found(DraftLine(pathway="dental", kind="routing"))

    body = [line for line in lines if line.kind != "routing"]
    dental = [line for line in body if line.pathway == "dental"]
    medical = [line for line in body if line.pathway == "medical"]
    not_found = [line for line in [routing, *body] if line.status == "not_found"]

    return Recommendation(
        case_id=case.case_id,
        date_of_service=case.date_of_service,
        date_of_service_altered=False,
        scenario=case.scenario,
        routing=routing,
        dental_lines=dental,
        medical_lines=medical,
        documentation_missing=_merge_gaps(gaps),
        guardrails=guardrails,
        not_found=not_found,
    )
