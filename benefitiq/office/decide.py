"""Turn yes / no / not sure answers into one cited result.

The question tree picks the branch. URLs and effective dates are reloaded from
the source registry. A line with no in-window source is shown as 'not found'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from benefitiq.db import session_scope
from benefitiq.engine.recommend import AUDIT, configure_audit_log
from benefitiq.ingestion.policy_text import excerpt
from benefitiq.labels import label_for
from benefitiq.office.tree import procedure_by_id
from benefitiq.payers.catalog import PROCEDURE_TOPICS
from benefitiq.payers.scope import plan_scope
from benefitiq.retrieval.search import CorpusRepo, Hit, Record

DISPLAY = {"yes": "Yes", "no": "No", "not_sure": "Not sure"}

# Short names for the one-page printout. The screen still shows the full title.
SHORT_SOURCE = {
    "cms-lcd-l33611": "LCD L33611",
    "cms-article-a52512": "Article A52512",
    "cms-bp102c15-150": "Manual Ch. 15 §150",
    "cms-medicare-dental": "CMS dental coverage",
    "cms-dental-exceptions": "CMS dental coverage",
    "oid-36-6060-6": "OID bulletin 36 O.S. § 6060.6",
}


@dataclass
class CitationView:
    status: str
    source_id: str | None = None
    url: str | None = None
    title: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    section: str | None = None
    short: str | None = None
    quote: str | None = None
    chunk_id: str | None = None
    version: int | None = None


@dataclass
class CodeView:
    status: str
    note: str
    code: str | None = None
    code_system: str | None = None
    label: str | None = None
    citations: list[CitationView] = field(default_factory=list)


@dataclass
class TextView:
    text: str
    citations: list[CitationView]


@dataclass
class AnswerView:
    prompt: str
    display: str


@dataclass
class OfficeDecision:
    case_id: str
    as_of: date
    procedure_id: str
    procedure_title: str
    rule_id: str
    result_label: str
    reason: TextView
    codes: list[CodeView]
    checklist: list[TextView]
    gaps: list[TextView]
    answers: list[AnswerView]
    cited_source_ids: list[str]
    sources: list[CitationView]
    payer_name: str | None = None
    segment_label: str | None = None
    payer_notice: str | None = None
    uncited_sentence: str | None = None
    retrieved: list[CitationView] = field(default_factory=list)
    policy_rows: list[dict] = field(default_factory=list)
    state_rows: list[dict] = field(default_factory=list)
    use_medicare_rules: bool = True
    fully_insured: str | None = None


def cite(repo: CorpusRepo, source_id: str | None, section: str | None, as_of: date) -> CitationView:
    """Reload one citation from the registry, or return not found."""
    if not source_id or not section:
        return CitationView(status="not_found")
    record = repo.get_record(source_id, f"{source_id}::{section}")
    if record is None or not record.url or record.effective_from is None:
        return CitationView(status="not_found", source_id=source_id, section=section)
    if record.effective_from > as_of:
        return CitationView(status="not_found", source_id=source_id, section=section)
    if record.effective_to is not None and record.effective_to < as_of:
        return CitationView(status="not_found", source_id=source_id, section=section)
    return CitationView(
        status="cited",
        source_id=record.source_id,
        url=record.url,
        title=record.title,
        effective_from=record.effective_from,
        effective_to=record.effective_to,
        section=record.section_anchor,
        short=SHORT_SOURCE.get(record.source_id, record.title),
        quote=excerpt(record.text) or None,
        chunk_id=record.chunk_id,
        version=record.version,
    )


def _resolve(
    repo: CorpusRepo,
    specs: list[dict[str, str]],
    as_of: date,
    allowed: set[str] | None = None,
) -> list[CitationView]:
    if not specs:
        return [CitationView(status="not_found")]
    views = [cite(repo, spec.get("source_id"), spec.get("section"), as_of) for spec in specs]
    if allowed is None:
        return views
    narrowed: list[CitationView] = []
    for view in views:
        if view.status == "cited" and view.source_id not in allowed:
            narrowed.append(CitationView(status="not_found", source_id=view.source_id, section=view.section))
        else:
            narrowed.append(view)
    return narrowed


RELEVANCE_FLOOR = 0.55


def _relevant(hit: Hit) -> bool:
    return hit.keyword_score > 0 or hit.vector_score >= RELEVANCE_FLOOR


def citation_from_record(record: Record) -> CitationView:
    """A citation built from one stored chunk, including a short quote."""
    return CitationView(
        status="cited",
        source_id=record.source_id,
        url=record.url,
        title=record.title,
        effective_from=record.effective_from,
        effective_to=record.effective_to,
        section=record.section_anchor,
        short=SHORT_SOURCE.get(record.source_id, record.title),
        quote=excerpt(record.text) or None,
        chunk_id=record.chunk_id,
        version=record.version,
    )


def retrieved_citations(
    repo: CorpusRepo,
    query: str,
    *,
    as_of: date,
    payer: str | None,
    segment: str | None,
    topics: tuple[str, ...] = (),
) -> list[CitationView]:
    """Cite current policy chunks for this plan. Nothing relevant is `not found`."""
    if not query.strip():
        return [CitationView(status="not_found")]
    topic_list: tuple[str | None, ...] = topics or (None,)
    seen: set[str] = set()
    views: list[CitationView] = []
    for topic in topic_list:
        hits = repo.hybrid_search(
            query,
            dos=as_of,
            payer=payer,
            segment=segment,
            topic=topic,
            limit=4,
        )
        for hit in hits:
            if hit.chunk_id in seen or not _relevant(hit):
                continue
            record = repo.get_record(hit.source_id, hit.chunk_id)
            if record is None or record.doc_type != "policy_text":
                continue
            seen.add(hit.chunk_id)
            views.append(citation_from_record(record))
    if not views:
        return [CitationView(status="not_found")]
    return views


def _cited(citations: list[CitationView]) -> bool:
    return any(item.status == "cited" for item in citations)


def _present_text(text: str, citations: list[CitationView]) -> TextView:
    if not _cited(citations):
        return TextView(text="not found", citations=citations or [CitationView(status="not_found")])
    return TextView(text=text, citations=citations)


def _present_code(repo: CorpusRepo, spec: dict, as_of: date, allowed: set[str] | None = None) -> CodeView:
    citations = _resolve(repo, spec.get("citations") or [], as_of, allowed)
    code = spec.get("code")
    if not _cited(citations):
        return CodeView(
            status="not_found",
            note="not found",
            code=code,
            code_system=spec.get("system"),
            label=label_for(code) if code else None,
            citations=citations,
        )
    return CodeView(
        status=spec["status"],
        note=spec["note"],
        code=code,
        code_system=spec.get("system"),
        label=label_for(code) if code else None,
        citations=citations,
    )


def _rule_matches(rule: dict, answers: dict[str, str]) -> bool:
    when = rule["when"]
    when_any = rule["when_any"]
    if when and not all(answers.get(clause["id"]) == clause["answer"] for clause in when):
        return False
    if when_any and not any(answers.get(clause["id"]) == clause["answer"] for clause in when_any):
        return False
    return bool(when or when_any)


def _source_ids(citations: list[CitationView], found: list[str]) -> None:
    for item in citations:
        if item.status == "cited" and item.source_id and item.source_id not in found:
            found.append(item.source_id)


def _policy_row(hit) -> dict:
    source = hit.source
    return {
        "source_id": source.source_id,
        "title": source.title,
        "number": source.number,
        "url": source.url,
        "date": source.date.iso,
        "date_label": source.date.label if source.date.value else "Date not found in the source list",
        "access": source.access,
        "access_label": source.access_label,
        "role": hit.role,
    }


def _state_row(source) -> dict:
    return {
        "source_id": source.source_id,
        "title": source.title,
        "number": source.number,
        "url": source.url,
        "date": source.date.iso,
        "date_label": source.date.label if source.date.value else "Date not found in the source list",
        "access_label": source.access_label,
    }


def _decide(
    repo: CorpusRepo,
    procedure: dict,
    answers: dict[str, str],
    *,
    as_of: date,
    case_id: str,
    payer_id: str | None = None,
    segment_id: str | None = None,
) -> OfficeDecision:
    scope = plan_scope(payer_id, segment_id, procedure["id"]) if payer_id and segment_id else None
    # An empty allow-list drops Medicare citations when that plan does not use them.
    allowed = set() if scope is not None and not scope.use_medicare_rules else None
    safe_answers = {key: value for key, value in answers.items() if value in DISPLAY}
    gaps: list[TextView] = []
    for question in procedure["questions"]:
        if safe_answers.get(question["id"]) in set(question["gap_on"]):
            gaps.append(
                _present_text(
                    f"documentation missing: {question['gap']}",
                    _resolve(repo, question["citations"], as_of, allowed),
                )
            )

    matched = next((rule for rule in procedure["rules"] if _rule_matches(rule, safe_answers)), None)
    if matched is None:
        result_id = "need_more_info"
        rule_id = "no-matching-rule"
        reason = _present_text("not found", [CitationView(status="not_found")])
        result_spec = procedure["results"][result_id]
        checklist = list(gaps) or [TextView("not found", [CitationView(status="not_found")])]
        code_specs = list(result_spec["codes"])
    else:
        result_id = matched["result"]
        rule_id = matched["id"]
        result_spec = procedure["results"][result_id]
        if matched["reason_from_gaps"]:
            pooled: list[CitationView] = []
            for gap in gaps:
                pooled.extend(gap.citations)
            reason = _present_text(matched["reason"], pooled or [CitationView(status="not_found")])
        else:
            reason = _present_text(
                matched["reason"],
                _resolve(repo, matched["reason_citations"], as_of, allowed),
            )
        if matched["checklist"]:
            checklist = [
                _present_text(item["text"], _resolve(repo, item["citations"], as_of, allowed))
                for item in matched["checklist"]
            ]
        elif result_spec["checklist_from_gaps"]:
            checklist = list(gaps) or [TextView("not found", [CitationView(status="not_found")])]
        else:
            checklist = [
                _present_text(item["text"], _resolve(repo, item["citations"], as_of, allowed))
                for item in result_spec["checklist"]
            ]
        code_specs = list(result_spec["codes"]) + list(matched["extra_codes"])

    codes = [_present_code(repo, spec, as_of, allowed) for spec in code_specs]
    retrieved = (
        retrieved_citations(
            repo,
            procedure["title"],
            as_of=as_of,
            payer=payer_id,
            segment=segment_id,
            topics=PROCEDURE_TOPICS.get(procedure["id"], ()),
        )
        if payer_id and segment_id
        else []
    )
    cited_retrieved = [item for item in retrieved if item.status == "cited"]
    if cited_retrieved and reason.text != "not found":
        known = {item.chunk_id for item in reason.citations}
        for item in cited_retrieved:
            if item.chunk_id not in known:
                reason.citations.append(item)
                known.add(item.chunk_id)
    answer_rows = [
        AnswerView(
            prompt=question["prompt"],
            display=DISPLAY.get(safe_answers.get(question["id"], ""), "Not answered"),
        )
        for question in procedure["questions"]
    ]
    cited: list[str] = []
    sources: list[CitationView] = []
    seen_urls: set[str] = set()

    def _collect(citations: list[CitationView]) -> None:
        _source_ids(citations, cited)
        for item in citations:
            if item.status == "cited" and item.url and item.url not in seen_urls:
                seen_urls.add(item.url)
                sources.append(item)

    _collect(reason.citations)
    for item in checklist + gaps:
        _collect(item.citations)
    for code in codes:
        _collect(code.citations)

    result_label = result_spec["label"]
    payer_name = None
    segment_label = None
    payer_notice = None
    uncited_sentence = None
    policy_rows: list[dict] = []
    state_rows: list[dict] = []
    use_medicare_rules = True
    fully_insured = None
    if scope is not None:
        payer_name = scope.payer_name
        segment_label = scope.segment_label
        payer_notice = scope.notice
        uncited_sentence = scope.uncited_sentence
        use_medicare_rules = scope.use_medicare_rules
        fully_insured = scope.fully_insured
        seen_ids: set[str] = set()
        for topic in scope.topics:
            for hit in topic.policies + topic.extras:
                if hit.source.source_id in seen_ids:
                    continue
                if hit.role == "baseline":
                    continue
                if (
                    not hit.source.counts_as_coverage
                    and hit.source.date.value is None
                    and hit.source.access == "public"
                ):
                    continue
                seen_ids.add(hit.source.source_id)
                policy_rows.append(_policy_row(hit))
        state_rows = [_state_row(item) for item in scope.state_rules]
        if not scope.use_medicare_rules:
            result_label = "Confirm with the payer"
    configure_audit_log()
    AUDIT.info(
        "office_result case_id=%s payer=%s sources=%s",
        case_id,
        payer_id or "none",
        ",".join(cited) if cited else "none",
    )
    return OfficeDecision(
        case_id=case_id,
        as_of=as_of,
        procedure_id=procedure["id"],
        procedure_title=procedure["title"],
        rule_id=rule_id,
        result_label=result_label,
        reason=reason,
        codes=codes,
        checklist=checklist,
        gaps=gaps,
        answers=answer_rows,
        cited_source_ids=cited,
        sources=sources,
        payer_name=payer_name,
        segment_label=segment_label,
        payer_notice=payer_notice,
        uncited_sentence=uncited_sentence,
        retrieved=cited_retrieved,
        policy_rows=policy_rows,
        state_rows=state_rows,
        use_medicare_rules=use_medicare_rules,
        fully_insured=fully_insured,
    )


def citations_for(
    specs: list[dict[str, str]],
    as_of: date,
    allowed: set[str] | None = None,
) -> list[CitationView]:
    with session_scope() as session:
        return _resolve(CorpusRepo(session), specs, as_of, allowed)


def decide(
    procedure_id: str,
    answers: dict[str, str],
    *,
    as_of: date,
    case_id: str,
    repo: CorpusRepo | None = None,
    payer_id: str | None = None,
    segment_id: str | None = None,
) -> OfficeDecision:
    procedure = procedure_by_id(procedure_id)
    if procedure is None or not procedure["active"]:
        raise KeyError(procedure_id)
    if repo is not None:
        return _decide(
            repo,
            procedure,
            answers,
            as_of=as_of,
            case_id=case_id,
            payer_id=payer_id,
            segment_id=segment_id,
        )
    with session_scope() as session:
        return _decide(
            CorpusRepo(session),
            procedure,
            answers,
            as_of=as_of,
            case_id=case_id,
            payer_id=payer_id,
            segment_id=segment_id,
        )
