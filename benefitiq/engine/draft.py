"""Draft types and the rules drafter.

An LLM drafter can replace RulesDrafter later. It must return the same Draft
shape. RecommendationService always runs hard guards and the citation validator
after drafting, so a model cannot publish an uncited line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from benefitiq.engine.evidence import Evidence
from benefitiq.retrieval.search import CorpusRepo, Record
from benefitiq.schemas import CaseInput


@dataclass
class DraftLine:
    pathway: str
    kind: str
    code_system: str | None = None
    code: str | None = None
    supporting_span_ids: list[str] = field(default_factory=list)
    source_id: str | None = None
    chunk_id: str | None = None
    requirement_element: str | None = None


@dataclass
class DraftGap:
    element: str
    related_code: str | None = None


@dataclass
class DraftGuard:
    type: str
    status: str
    codes: list[str]
    message: str
    missing_elements: list[str] = field(default_factory=list)
    source_id: str | None = None
    chunk_id: str | None = None


@dataclass
class Draft:
    lines: list[DraftLine] = field(default_factory=list)
    gaps: list[DraftGap] = field(default_factory=list)
    guards: list[DraftGuard] = field(default_factory=list)


class Drafter(Protocol):
    def draft(self, case: CaseInput, repo: CorpusRepo, evidence: Evidence) -> Draft:
        """Return candidate lines. Citations are ids only; URLs are loaded later."""


def _requires(record: Record) -> list[str]:
    value = record.metadata.get("requires") or []
    return [str(item) for item in value]


def _supported(record: Record, evidence: Evidence) -> bool:
    return set(_requires(record)).issubset(evidence.elements)


def _add_gaps(draft: Draft, elements: list[str], code: str | None) -> None:
    for element in elements:
        draft.gaps.append(DraftGap(element=element, related_code=code))


def _payers(case: CaseInput) -> list[str]:
    return [payer for payer in (case.payer_dental, case.payer_medical) if payer]


def _pathway_payer(case: CaseInput, pathway: str) -> str | None:
    if pathway == "dental":
        return case.payer_dental
    if pathway == "medical":
        return case.payer_medical
    return None


class RulesDrafter:
    """Deterministic drafter. It proposes only rows that exist in the corpus."""

    def draft(self, case: CaseInput, repo: CorpusRepo, evidence: Evidence) -> Draft:
        draft = Draft()
        payers = _payers(case)
        if not repo.scenario_has_records(case.scenario, case.date_of_service):
            self._unknown_scenario(case, draft)
            return draft

        self._routing(case, repo, evidence, draft, payers)
        self._coding(case, repo, evidence, draft, payers)
        self._coverage(case, repo, evidence, draft)
        self._checklist(case, repo, evidence, draft, payers)
        self._requested_without_support(case, draft)
        return draft

    def _unknown_scenario(self, case: CaseInput, draft: Draft) -> None:
        for code in case.requested_codes:
            pathway = "dental" if code.system == "CDT" else "medical"
            for kind in ("coding", "coverage"):
                draft.lines.append(
                    DraftLine(
                        pathway=pathway,
                        kind=kind,
                        code_system=code.system,
                        code=code.code,
                    )
                )
        draft.lines.append(DraftLine(pathway="dental", kind="routing"))
        draft.lines.append(DraftLine(pathway="medical", kind="coverage"))
        if not any(line.pathway == "dental" and line.kind == "coverage" for line in draft.lines):
            draft.lines.append(DraftLine(pathway="dental", kind="coverage"))

    def _routing(self, case, repo, evidence, draft: Draft, payers: list[str]) -> None:
        rules = repo.records(
            "routing_rule",
            scenario=case.scenario,
            dos=case.date_of_service,
            state=case.state,
            plan_type=case.plan_type,
            payers=payers,
            payer_mode="match_or_general",
        )
        matched = [rule for rule in rules if _supported(rule, evidence)]
        matched.sort(key=lambda rule: str(rule.metadata.get("route_id")))
        if matched:
            rule = matched[0]
            draft.lines.append(
                DraftLine(
                    pathway=str(rule.metadata.get("primary_pathway") or "dental"),
                    kind="routing",
                    supporting_span_ids=evidence.spans_for(_requires(rule)),
                    source_id=rule.source_id,
                    chunk_id=rule.chunk_id,
                )
            )
            return
        if rules:
            missing = [item for item in _requires(rules[0]) if item not in evidence.elements]
            _add_gaps(draft, missing, None)
        draft.lines.append(DraftLine(pathway="dental", kind="routing"))

    def _coding(self, case, repo, evidence, draft: Draft, payers: list[str]) -> None:
        requested = {(code.system, code.code) for code in case.requested_codes}
        rules = repo.records(
            "coding_rule",
            scenario=case.scenario,
            dos=case.date_of_service,
            state=case.state,
            plan_type=case.plan_type,
            payers=payers,
            payer_mode="match_or_general",
        )
        for rule in rules:
            code = str(rule.metadata.get("code"))
            system = str(rule.metadata.get("code_system"))
            is_requested = (system, code) in requested
            volunteer = bool(rule.metadata.get("volunteer"))
            if _supported(rule, evidence) and (is_requested or volunteer):
                draft.lines.append(
                    DraftLine(
                        pathway=str(rule.metadata.get("pathway") or "dental"),
                        kind="coding",
                        code_system=system,
                        code=code,
                        supporting_span_ids=evidence.spans_for(_requires(rule)),
                        source_id=rule.source_id,
                        chunk_id=rule.chunk_id,
                    )
                )
                continue
            if not _supported(rule, evidence) and (is_requested or volunteer or rule.metadata.get("report_gap")):
                missing = [item for item in _requires(rule) if item not in evidence.elements]
                _add_gaps(draft, missing, code)

    def _coverage(self, case, repo, evidence, draft: Draft) -> None:
        rules = repo.records(
            "coverage_rule",
            scenario=case.scenario,
            dos=case.date_of_service,
            state=case.state,
            plan_type=case.plan_type,
            payers=_payers(case),
            payer_mode="match",
        )
        requested = {(code.system, code.code) for code in case.requested_codes}
        for rule in rules:
            pathway = str(rule.metadata.get("pathway") or "dental")
            if rule.payer != _pathway_payer(case, pathway):
                continue
            code = str(rule.metadata.get("code"))
            system = str(rule.metadata.get("code_system"))
            if requested and (system, code) not in requested:
                continue
            if _supported(rule, evidence):
                draft.lines.append(
                    DraftLine(
                        pathway=pathway,
                        kind="coverage",
                        code_system=system,
                        code=code,
                        supporting_span_ids=evidence.spans_for(_requires(rule)),
                        source_id=rule.source_id,
                        chunk_id=rule.chunk_id,
                    )
                )
            else:
                missing = [item for item in _requires(rule) if item not in evidence.elements]
                _add_gaps(draft, missing, code)

    def _checklist(self, case, repo, evidence, draft: Draft, payers: list[str]) -> None:
        items = repo.records(
            "checklist",
            scenario=case.scenario,
            dos=case.date_of_service,
            state=case.state,
            plan_type=case.plan_type,
            payers=payers,
            payer_mode="match_or_general",
        )
        for item in sorted(items, key=lambda record: str(record.metadata.get("item_id"))):
            element = str(item.metadata.get("element") or "")
            pathway = str(item.metadata.get("pathway") or "medical")
            if item.payer is not None and item.payer != _pathway_payer(case, pathway):
                continue
            draft.lines.append(
                DraftLine(
                    pathway=pathway,
                    kind="requirement",
                    requirement_element=element,
                    supporting_span_ids=evidence.spans_for([element]),
                    source_id=item.source_id,
                    chunk_id=item.chunk_id,
                )
            )

    def _requested_without_support(self, case: CaseInput, draft: Draft) -> None:
        covered_codes = {line.code for line in draft.lines if line.code}
        gap_codes = {gap.related_code for gap in draft.gaps if gap.related_code}
        for code in case.requested_codes:
            if code.code in covered_codes or code.code in gap_codes:
                continue
            pathway = "dental" if code.system == "CDT" else "medical"
            for kind in ("coding", "coverage"):
                draft.lines.append(
                    DraftLine(
                        pathway=pathway,
                        kind=kind,
                        code_system=code.system,
                        code=code.code,
                    )
                )
