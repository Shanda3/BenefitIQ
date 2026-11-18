"""Hard guards. These run after every drafter, including a future LLM drafter."""

from __future__ import annotations

from benefitiq.constants import EXAM_RANK, EXAM_REQUIRED
from benefitiq.engine.draft import Draft, DraftGap, DraftGuard, DraftLine
from benefitiq.engine.evidence import Evidence
from benefitiq.retrieval.search import CorpusRepo, Record
from benefitiq.schemas import CaseInput, CodeRef


def documented_exam_rank(evidence: Evidence) -> int:
    rank = 0
    for code, required in EXAM_REQUIRED.items():
        if all(element in evidence.elements for element in required):
            rank = max(rank, EXAM_RANK[code])
    return rank


def _candidate_codes(case: CaseInput, draft: Draft) -> list[CodeRef]:
    seen: set[tuple[str, str]] = set()
    codes: list[CodeRef] = []
    for code in case.requested_codes:
        key = (code.system, code.code)
        if key not in seen:
            seen.add(key)
            codes.append(code)
    for line in draft.lines:
        if line.code and line.code_system:
            key = (line.code_system, line.code)
            if key not in seen:
                seen.add(key)
                codes.append(CodeRef(system=line.code_system, code=line.code))
    return codes


def _coding_rule(repo: CorpusRepo, case: CaseInput, code: str) -> Record | None:
    payers = [payer for payer in (case.payer_dental, case.payer_medical) if payer]
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
        if rule.metadata.get("code") == code:
            return rule
    return None


def apply_hard_guards(case: CaseInput, evidence: Evidence, draft: Draft, repo: CorpusRepo) -> Draft:
    """Drop unsupported higher-level codes, malignancy codes, and unbundled lines."""
    guards = list(draft.guards)
    strip: set[str] = set()

    if (
        case.alternate_date_of_service is not None
        and case.alternate_date_of_service != case.date_of_service
    ):
        guards.append(
            DraftGuard(
                type="date_of_service",
                status="refused",
                codes=[],
                message=(
                    "Refused request to change the date of service. "
                    "The documented date of service was left unchanged."
                ),
            )
        )

    for code in _candidate_codes(case, draft):
        if code.system == "ICD10CM" and code.code.startswith("C"):
            if "pathology_confirms_malignancy" not in evidence.elements:
                guards.append(
                    DraftGuard(
                        type="malignancy",
                        status="refused",
                        codes=[code.code],
                        message=(
                            "Refused a malignancy code because the documentation "
                            "does not include a confirming pathology report."
                        ),
                    )
                )
                strip.add(code.code)
                if "pathology_report" not in evidence.elements:
                    draft.gaps.append(DraftGap(element="pathology_report", related_code=code.code))

    documented_rank = documented_exam_rank(evidence)
    refused_exam: list[str] = []
    for code in _candidate_codes(case, draft):
        if code.code not in EXAM_RANK:
            continue
        if EXAM_RANK[code.code] <= documented_rank:
            continue
        missing = [element for element in EXAM_REQUIRED[code.code] if element not in evidence.elements]
        rule = _coding_rule(repo, case, code.code)
        guards.append(
            DraftGuard(
                type="upcoding",
                status="refused",
                codes=[code.code],
                message="Refused a higher-level code than the documentation supports.",
                missing_elements=missing,
                source_id=None if rule is None else rule.source_id,
                chunk_id=None if rule is None else rule.chunk_id,
            )
        )
        for element in missing:
            draft.gaps.append(DraftGap(element=element, related_code=code.code))
        strip.add(code.code)
        refused_exam.append(code.code)

    if refused_exam and documented_rank > 0:
        supported = [
            code
            for code, rank in EXAM_RANK.items()
            if rank == documented_rank and all(element in evidence.elements for element in EXAM_REQUIRED[code])
        ]
        for code in supported:
            if any(line.code == code for line in draft.lines):
                continue
            rule = _coding_rule(repo, case, code)
            if rule is None:
                continue
            required = [str(item) for item in (rule.metadata.get("requires") or [])]
            if not set(required).issubset(evidence.elements):
                continue
            draft.lines.append(
                DraftLine(
                    pathway=str(rule.metadata.get("pathway") or "dental"),
                    kind="coding",
                    code_system=str(rule.metadata.get("code_system") or "CDT"),
                    code=code,
                    supporting_span_ids=evidence.spans_for(required),
                    source_id=rule.source_id,
                    chunk_id=rule.chunk_id,
                )
            )

    payers = [payer for payer in (case.payer_dental, case.payer_medical) if payer]
    requested = {code.code for code in case.requested_codes}
    if payers:
        for rule in repo.bundle_rules(
            dos=case.date_of_service,
            payers=payers,
            state=case.state,
            plan_type=case.plan_type,
        ):
            bundle_codes = [str(code) for code in rule.metadata.get("codes") or []]
            overlap = [code for code in bundle_codes if code in requested]
            if len(overlap) < 2:
                continue
            guards.append(
                DraftGuard(
                    type="unbundling",
                    status="flagged",
                    codes=bundle_codes,
                    message=(
                        "Bundled components were entered as separate codes. "
                        "Separate reporting was not recommended."
                    ),
                    source_id=rule.source_id,
                    chunk_id=rule.chunk_id,
                )
            )
            strip.update(bundle_codes)

    draft.lines = [line for line in draft.lines if line.code not in strip]
    draft.guards = guards
    return draft
