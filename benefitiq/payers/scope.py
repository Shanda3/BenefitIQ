"""Which registered sources apply to a payer, a plan, and a procedure.

Medicare rules stay in view for Medicare Advantage and Medicare fee-for-service.
SoonerCare rules stay in view for SoonerSelect and fee-for-service. A fully
insured plan also sees the state anesthesia rule. A licensed criteria set is
named as not public. It is not turned into a coverage answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from benefitiq.payers.catalog import (
    DISPLAY_TOPICS,
    LICENSED_SENTENCE,
    PENDING_ACCESS,
    PROCEDURE_TOPICS,
    PolicySource,
    linked_sources,
    payer_by_id,
    segment_by_id,
    sources,
)

MEDICARE_PAYERS = ("medicare-ffs", "medicare-novitas", "medicare-cgs")
MEDICAID_PAYERS = ("sooner-ohca",)


@dataclass(frozen=True)
class PolicyHit:
    source: PolicySource
    role: str


@dataclass(frozen=True)
class TopicCoverage:
    topic_id: str
    label: str
    policies: tuple[PolicyHit, ...]
    extras: tuple[PolicyHit, ...]
    notice: str


@dataclass(frozen=True)
class PlanScope:
    payer_id: str
    payer_name: str
    segment_id: str
    segment_label: str
    use_medicare_rules: bool
    fully_insured: str
    topics: tuple[TopicCoverage, ...]
    state_rules: tuple[PolicySource, ...]
    notice: str
    uncited_sentence: str | None


def payer_gap(payer_name: str) -> str:
    return f"Our sources don't cover this for {payer_name} yet. Confirm with the payer."


def _policies_notice(payer_name: str) -> str:
    return f"Policies on file for {payer_name}. Confirm them with the payer before billing."


def _role(source: PolicySource, payer_id: str, segment_id: str) -> str | None:
    for link in source.links:
        if link.payer_id == payer_id and link.segment_id == segment_id:
            return link.role
    return None


def _matches(source: PolicySource, topic_ids: set[str] | None) -> bool:
    if topic_ids is None:
        return True
    if not source.topics:
        return source.access == "licensed"
    return bool(set(source.topics) & topic_ids)


def _collect(payer_id: str, segment_id: str, topic_ids: set[str] | None) -> list[PolicyHit]:
    hits: list[PolicyHit] = []
    seen: set[str] = set()
    for source in linked_sources(payer_id, segment_id):
        if not _matches(source, topic_ids):
            continue
        role = _role(source, payer_id, segment_id) or "primary"
        hits.append(PolicyHit(source, role))
        seen.add(source.source_id)
    return hits


def _baseline(payer_ids: tuple[str, ...], topic_ids: set[str] | None) -> list[PolicyHit]:
    hits: list[PolicyHit] = []
    for source in sources():
        if source.payer_id not in payer_ids or source.access == "not_applicable":
            continue
        if topic_ids is not None and not (set(source.topics) & topic_ids):
            continue
        if not source.counts_as_coverage and source.date.value is None:
            continue
        hits.append(PolicyHit(source, "baseline"))
    return hits


def _topic_notice(payer_name: str, policies: list[PolicyHit], extras: list[PolicyHit]) -> str:
    licensed = any(hit.source.access == "licensed" for hit in extras)
    pending = [hit for hit in extras if hit.source.access == PENDING_ACCESS]
    fallbacks = [hit for hit in policies if hit.role == "fallback"]
    own = [hit for hit in policies if hit.role != "fallback"]
    if own:
        return _policies_notice(payer_name)
    if licensed:
        notice = LICENSED_SENTENCE
        if fallbacks:
            notice += " Published bulletins from the parent company are listed as a fallback. They are not this plan's own criteria."
        return notice
    if pending:
        notice = PENDING_ACCESS
        if any(hit.source.unconfirmed for hit in pending):
            notice += " Whether the title addresses this procedure was not confirmed, because the document was not opened."
        return notice
    if fallbacks:
        return (
            f"No Oklahoma plan policy for {payer_name} was found. "
            "Published bulletins from the parent company are listed as a fallback. Confirm with the payer."
        )
    return payer_gap(payer_name)


def _state_rules(fully_insured: str) -> tuple[PolicySource, ...]:
    if fully_insured not in {"yes", "mixed"}:
        return ()
    return tuple(
        source
        for source in sources()
        if source.payer_id == "oklahoma-fully-insured" and source.access != "not_applicable"
    )


def plan_scope(payer_id: str, segment_id: str, procedure_id: str | None = None) -> PlanScope | None:
    payer = payer_by_id(payer_id)
    if payer is None:
        return None
    segment = segment_by_id(payer, segment_id)
    if segment is None:
        return None
    topic_filter = PROCEDURE_TOPICS.get(procedure_id) if procedure_id else None
    wanted = set(topic_filter) if topic_filter else None
    labels = dict(DISPLAY_TOPICS)
    if wanted is None:
        topic_ids = [topic_id for topic_id, _label in DISPLAY_TOPICS]
    else:
        topic_ids = [topic_id for topic_id in topic_filter if topic_id in labels]

    use_medicare = bool(segment.get("medicare"))
    medicaid = bool(segment.get("medicaid")) or segment_id in {"medicaid_ffs", "soonerselect_medical", "soonerselect_dental"}
    fully_insured = str(segment.get("fully_insured") or "no")
    topics: list[TopicCoverage] = []
    for topic_id in topic_ids:
        topic_set = {topic_id}
        hits = _collect(payer_id, segment_id, topic_set)
        if use_medicare:
            hits.extend(_baseline(MEDICARE_PAYERS, topic_set))
        if medicaid:
            hits.extend(_baseline(MEDICAID_PAYERS, topic_set))
        policies = tuple(hit for hit in hits if hit.source.counts_as_coverage)
        extras = tuple(hit for hit in hits if not hit.source.counts_as_coverage)
        topics.append(
            TopicCoverage(
                topic_id=topic_id,
                label=labels[topic_id],
                policies=policies,
                extras=extras,
                notice=_topic_notice(payer["name"], list(policies), list(extras)),
            )
        )

    if procedure_id:
        own = [hit for topic in topics for hit in topic.policies if hit.role != "baseline"]
        extras = [hit for topic in topics for hit in topic.extras]
        notice = _topic_notice(payer["name"], own, extras)
        # Medicare rules are a real cited source. Do not say the library has nothing.
        if use_medicare and any(
            hit.role == "baseline" and hit.source.counts_as_coverage for topic in topics for hit in topic.policies
        ):
            primary = [hit for hit in own if hit.role not in {"baseline", "fallback"}]
            if not primary:
                medicare_line = (
                    f"Medicare rules apply to {payer['name']}. "
                    "No separate plan policy for this procedure was found."
                )
                if notice == payer_gap(payer["name"]):
                    notice = medicare_line + " Confirm with the payer before billing."
                else:
                    notice = medicare_line + " " + notice
        # Medicare citations stay on the page. Every other plan replaces an uncited
        # Medicare sentence with the plan notice, including "policies on file."
        uncited = None if use_medicare else notice
    else:
        notice = ""
        uncited = None

    rules = _state_rules(fully_insured)
    if procedure_id and "anesthesia" not in set(topic_filter or ()):
        rules = ()

    return PlanScope(
        payer_id=payer_id,
        payer_name=payer["name"],
        segment_id=segment_id,
        segment_label=segment["label"],
        use_medicare_rules=use_medicare,
        fully_insured=fully_insured,
        topics=tuple(topics),
        state_rules=rules,
        notice=notice,
        uncited_sentence=uncited,
    )
