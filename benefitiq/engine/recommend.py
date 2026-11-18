"""Recommendation pipeline.

RulesDrafter is the skeleton implementation. Swap in another Drafter to add an
LLM drafting step; hard guards and the citation validator still run and still
drop anything they cannot tie to documentation and an in-window source.
"""

from __future__ import annotations

import logging

from benefitiq.db import session_scope
from benefitiq.engine.draft import Drafter, RulesDrafter
from benefitiq.engine.evidence import extract_evidence
from benefitiq.engine.guards import apply_hard_guards
from benefitiq.engine.validator import finalize
from benefitiq.retrieval.search import CorpusRepo
from benefitiq.schemas import CaseInput, Recommendation

AUDIT = logging.getLogger("benefitiq.audit")


def configure_audit_log() -> None:
    """Emit case-id lines even when the process only logs warnings by default."""
    AUDIT.setLevel(logging.INFO)
    if AUDIT.handlers:
        return
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    AUDIT.addHandler(handler)


def evaluate(case: CaseInput, drafter: Drafter | None = None) -> Recommendation:
    # Case id only. Clinical note text is never written to the log.
    configure_audit_log()
    AUDIT.info("case_evaluated case_id=%s", case.case_id)
    drafter = drafter or RulesDrafter()
    with session_scope() as session:
        repo = CorpusRepo(session)
        evidence = extract_evidence(case.spans)
        draft = drafter.draft(case, repo, evidence)
        draft = apply_hard_guards(case, evidence, draft, repo)
        return finalize(case, evidence, draft, repo)
