"""Load the Oklahoma payer list and decide what may be registered or fetched.

The CSV is the policy inventory. enrollment.yaml is the only place enrollment
numbers live. A row that is already in the office corpus keeps its source id.
The terms-gated BCBSOK library is not registered. Article A59449 is registered
only so it can be marked not applicable to Oklahoma.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from datetime import date

from benefitiq.payers.dates import SourceDate, parse_source_date

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "corpus" / "payers" / "oklahoma_payers.csv"
ENROLLMENT_PATH = ROOT / "corpus" / "payers" / "enrollment.yaml"

PENDING_ACCESS = "pending: document access requires disclaimer acceptance by Shanda"
LICENSED_SENTENCE = "This payer's criteria aren't public. Confirm with the payer."

PAYER_IDS = {
    "Blue Cross and Blue Shield of Oklahoma (BCBSOK)": "bcbsok",
    "Aetna (CVS Health)": "aetna",
    "Aetna Better Health of Oklahoma": "aetna-better-health",
    "UnitedHealthcare": "uhc",
    "Cigna Healthcare": "cigna",
    "Oklahoma Complete Health": "och",
    "Ambetter of Oklahoma": "ambetter",
    "Centene Dental (Envolve)": "centene-dental",
    "Humana Healthy Horizons in Oklahoma": "humana-ok",
    "Humana": "humana",
    "CommunityCare of Oklahoma": "communitycare",
    "CommunityCare Senior Health Plan": "communitycare",
    "GlobalHealth": "globalhealth",
    "Oscar Insurance Company": "oscar",
    "HealthChoice (OMES EGID)": "healthchoice",
    "TRICARE West (TriWest Healthcare Alliance)": "tricare-west",
    "TRICARE (Defense Health Agency)": "tricare-west",
    "FEP Blue (BCBS Federal Employee Program)": "fep-blue",
    "Medicare fee-for-service (CMS)": "medicare-ffs",
    "Medicare DME MAC Jurisdiction C (CGS Administrators)": "medicare-cgs",
    "Medicare A/B MAC Jurisdiction H (Novitas Solutions)": "medicare-novitas",
    "Medicare (CMS) - NOT Oklahoma MAC": "medicare-palmetto",
    "SoonerCare (Oklahoma Health Care Authority)": "sooner-ohca",
    "LIBERTY Dental Plan of Oklahoma": "liberty",
    "DentaQuest of Oklahoma": "dentaquest",
    "All fully insured Oklahoma plans (state mandate)": "oklahoma-fully-insured",
}

# Dates already stored on the office corpus. The CSV's "page last modified"
# stamp is not the claim-rule date the office source uses.
OFFICE_DATES = {
    "cms-lcd-l33611": SourceDate(date(2021, 8, 8), "effective", "Revision effective"),
    "cms-article-a52512": SourceDate(date(2021, 8, 8), "effective", "Revision effective"),
    "cms-bp102c15-150": SourceDate(date(2023, 1, 1), "effective", "effective"),
    "cms-medicare-dental": SourceDate(date(2025, 7, 1), "effective", "Claim rule effective"),
    "cms-dental-exceptions": SourceDate(date(2025, 7, 1), "effective", "Claim rule effective"),
    "oid-36-6060-6": SourceDate(
        date(2010, 1, 12),
        "document_date",
        "Date (not labeled effective or last review)",
    ),
}

EXISTING_IDS = {
    "L33611": "cms-lcd-l33611",
    "A52512": "cms-article-a52512",
    "IOM 100-02 Ch.15 §150": "cms-bp102c15-150",
    "CMS Medicare Dental Coverage page": "cms-medicare-dental",
    "36 O.S. § 6060.6": "oid-36-6060-6",
}

ACCESS_LABELS = {
    "public": "Public",
    PENDING_ACCESS: "Pending document access",
    "licensed": "Criteria aren't public",
    "login": "Login required",
    "unverified": "Web address not verified",
    "not_applicable": "Does not apply to Oklahoma",
}

DISPLAY_TOPICS = (
    ("sleep_appliance", "Sleep apnea mouthpiece"),
    ("sleep_test", "Sleep test for sleep apnea"),
    ("osa_surgery", "Surgery for sleep apnea"),
    ("tmj", "Jaw joint (TMJ)"),
    ("orthognathic", "Jaw surgery"),
    ("cleft", "Cleft and reconstructive care"),
    ("frenectomy", "Frenectomy"),
    ("biopsy", "Biopsy"),
    ("cbct", "CBCT scan"),
    ("anesthesia", "Dental anesthesia"),
    ("accidental", "Accidental dental injury"),
    ("linked_dental", "Dental care tied to a medical treatment"),
    ("bone_graft", "Bone graft"),
)

PROCEDURE_TOPICS = {
    "sleep_appliance": ("sleep_appliance", "sleep_test"),
    "medicare_dental": ("linked_dental", "accidental"),
    "tmj": ("tmj",),
    "injury_trauma": ("accidental", "linked_dental"),
    "biopsy": ("biopsy",),
    "cbct": ("cbct", "linked_dental"),
    "ridge_reconstruction": ("bone_graft", "linked_dental"),
    "ok_dental_anesthesia": ("anesthesia",),
}

GROUP_LABELS = {
    "employer": "Employer plans",
    "marketplace": "Individual and Marketplace plans",
    "soonerselect": "SoonerCare",
    "medicare_advantage": "Medicare Advantage",
    "medicare": "Medicare fee-for-service",
    "other": "HealthChoice, TRICARE, and federal employees",
    "reference": "Reference only",
}


@dataclass(frozen=True)
class Link:
    payer_id: str
    segment_id: str
    role: str = "primary"


@dataclass(frozen=True)
class PolicySource:
    source_id: str
    payer_id: str
    title: str
    number: str
    url: str
    topic: str
    topics: tuple[str, ...]
    date: SourceDate
    access: str
    fetch: bool
    fetch_reason: str
    counts_as_coverage: bool
    unconfirmed: bool
    disposition: str
    publisher: str
    doc_type: str
    links: tuple[Link, ...] = field(default_factory=tuple)
    state: str = "OK"
    plan_type: str = "ok-payer"
    program: str = "oklahoma-payer"

    @property
    def access_label(self) -> str:
        return ACCESS_LABELS.get(self.access, self.access)

    @property
    def citable(self) -> bool:
        return self.disposition == "added" and self.date.value is not None


@dataclass(frozen=True)
class SkippedSource:
    title: str
    url: str
    reason: str


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def _source_id(payer_id: str, number: str, title: str) -> str:
    number_slug = _slug(number) if number and number.lower() != "n/a" else ""
    title_slug = _slug(title)[:48]
    body = "-".join(part for part in (number_slug, title_slug) if part)
    return f"ok-{payer_id}-{body}"[:160]


def _topics(topic: str, title: str) -> tuple[str, ...]:
    text = f"{topic} {title}".lower()
    # A library page that says it has no dental policy is not a policy for those topics.
    if "library" in topic.lower() or "no dental" in text or "none dental" in text:
        return ()
    found: list[str] = []

    def add(key: str, *needles: str) -> None:
        if any(needle in text for needle in needles):
            found.append(key)

    add("sleep_appliance", "osa oral appliance", "oral appliance")
    add("sleep_test", "osa diagnosis", "sleep test", "sleep stud")
    add("osa_surgery", "osa surgery", "sleep apnea surgery")
    add("tmj", "tmj", "tmd", "temporomandibular")
    add("orthognathic", "orthognathic")
    add("cleft", "cleft", "reconstructive", "prosthet")
    add("frenectomy", "frenectomy", "frenulectomy", "oral function")
    add("biopsy", "biopsy", "oral lesion")
    add("cbct", "cbct", "cone beam", "radiographic")
    add("anesthesia", "anesthesia", "sedation")
    add("accidental", "accidental")
    add("linked_dental", "dental services related to medical", "adjunctive dental")
    add("bone_graft", "bone graft", "alveoloplasty", "ridge")
    return tuple(dict.fromkeys(found))


def _existing_id(row: dict[str, str]) -> str | None:
    number = (row.get("policy_number") or "").strip()
    title = (row.get("policy_title") or "").strip()
    if number in EXISTING_IDS:
        return EXISTING_IDS[number]
    if title in EXISTING_IDS:
        return EXISTING_IDS[title]
    return None


def _access(row: dict[str, str]) -> str:
    url = (row.get("url") or "").lower()
    notes = row.get("access_notes") or ""
    public = row.get("public_without_login") or ""
    payer = row.get("payer") or ""
    number = (row.get("policy_number") or "").strip()
    if "not oklahoma" in payer.lower() or number == "A59449" or "not applicable" in notes.lower():
        return "not_applicable"
    if "mcp.humana.com" in url or "disclaimer" in public.lower():
        return PENDING_ACCESS
    if public.lower().startswith("no") or "login-gated" in notes.lower() or "login" in public.lower():
        return "login"
    if public.strip().lower() == "unverified" or notes.upper().startswith("UNVERIFIED"):
        return "unverified"
    if any(token in notes for token in ("MCG", "InterQual", "eviCore")):
        return "licensed"
    return "public"


def blocked_fetch_reason(url: str) -> str | None:
    """URLs this project must not download."""
    lowered = url.lower()
    host_blocks = (
        ("medicalpolicy.bcbsok.com", "BCBSOK terms-gated policy library"),
        ("connections.cigna.com", "Cigna index site disallows robots"),
        ("mcp.humana.com", "Humana document access requires a disclaimer"),
        ("manuals.health.mil", "TRICARE Policy Manual address is unverified"),
        ("manuals.dha.mil", "TRICARE Policy Manual address is unverified"),
        ("provideraccess.dentaquest.com", "DentaQuest manual is login-only"),
    )
    for host, reason in host_blocks:
        if host in lowered:
            return reason
    if "ccok.com" in lowered or "ccokadvantage.com" in lowered:
        if "/pdf/" in lowered or "/assets/" in lowered:
            return "CommunityCare robots.txt disallows this folder"
    return None


def _counts(title: str, topic: str, access: str, dated: bool, unconfirmed: bool) -> bool:
    if access != "public" or not dated or unconfirmed:
        return False
    if "library" in topic.lower() or "no dental" in topic.lower():
        return False
    lowered = title.lower()
    blockers = (
        "index",
        "library",
        "landing",
        "news",
        "guideline",
        "prior authorization",
        "provider reference manual",
        "provider manual",
        "reference guide",
        "provider resources",
    )
    if any(word in lowered for word in blockers):
        return False
    if "manual" in lowered and "handbook" not in lowered:
        return False
    return True


def _doc_type(title: str) -> str:
    lowered = title.lower()
    if any(word in lowered for word in ("index", "library", "landing", "page")):
        if "handbook" not in lowered and "bulletin" not in lowered:
            return "index"
    if any(word in lowered for word in ("manual", "handbook", "guide")):
        return "manual"
    if lowered.startswith("oac") or " oac " in f" {lowered} ":
        return "rule"
    return "policy"


def _segment_ids(payer_id: str, segment_text: str, available: set[str]) -> list[str]:
    text = segment_text.lower()
    chosen: list[str] = []

    def take(*ids: str) -> None:
        for item in ids:
            if item in available and item not in chosen:
                chosen.append(item)

    if "dental" in text and payer_id == "aetna":
        take("aetna_dental")
        return chosen
    if "dental" in text and payer_id == "uhc":
        take("uhc_dental")
        return chosen
    if "not an ok" in text or "reference" in text:
        take("reference")
        return chosen
    if "soonerselect dental" in text or ("dental" in text and payer_id in {"liberty", "dentaquest"}):
        take("soonerselect_dental")
        return chosen
    if "fully insured" in text or payer_id == "oklahoma-fully-insured":
        take("state_rule")
        return chosen
    if "tricare" in text or payer_id == "tricare-west":
        take("tricare")
        return chosen
    if "fehb" in text or "pshb" in text or payer_id == "fep-blue":
        take("fehb")
        return chosen
    if "healthchoice" in text or "state/education" in text or payer_id == "healthchoice":
        take("healthchoice")
        return chosen
    if "dme" in text or payer_id == "medicare-cgs":
        take("medicare_dme")
        return chosen
    if "part a/b" in text or "jurisdiction h" in text or payer_id == "medicare-novitas":
        take("medicare_ab")
        return chosen
    if payer_id == "medicare-palmetto":
        take("not_oklahoma")
        return chosen
    if "medicare ffs" in text or payer_id == "medicare-ffs":
        take("medicare_ffs")
        return chosen
    if "medicare advantage" in text:
        take("medicare_advantage")
    if "soonerselect" in text or "medicaid mco" in text:
        take("soonerselect_medical")
    if "medicaid" in text or payer_id == "sooner-ohca":
        take("medicaid_ffs")
    if "commercial" in text or "employer" in text:
        take("large_group", "small_group", "commercial")
    if "marketplace" in text or "aca " in text:
        take("marketplace", "individual")
    if "individual" in text:
        take("individual")
    if not chosen:
        take(*sorted(available))
    return chosen


def _fallback_links(number: str, links: list[Link]) -> list[Link]:
    """Aetna medical bulletins are the published fallback for Aetna Better Health."""
    if not number.startswith("CPB "):
        return links
    extra = Link("aetna-better-health", "soonerselect_medical", "fallback")
    if extra in links:
        return links
    return [*links, extra]


@lru_cache(maxsize=1)
def enrollment() -> dict:
    return yaml.safe_load(ENROLLMENT_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def payers() -> tuple[dict, ...]:
    return tuple(enrollment()["payers"])


def payer_by_id(payer_id: str) -> dict | None:
    for payer in payers():
        if payer["id"] == payer_id:
            return payer
    return None


def segment_by_id(payer: dict, segment_id: str) -> dict | None:
    for segment in payer["segments"]:
        if segment["id"] == segment_id:
            return segment
    return None


def market_by_id(market_id: str) -> dict | None:
    for market in enrollment().get("markets") or []:
        if market["id"] == market_id:
            return market
    return None


@lru_cache(maxsize=1)
def inventory() -> tuple[tuple[PolicySource, ...], tuple[SkippedSource, ...]]:
    available = {payer["id"]: {segment["id"] for segment in payer["segments"]} for payer in payers()}
    sources: list[PolicySource] = []
    skipped: list[SkippedSource] = []
    seen: set[str] = set()
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("url") or "").strip()
            title = (row.get("policy_title") or "").strip()
            number = (row.get("policy_number") or "").strip()
            payer_name = (row.get("payer") or "").strip()
            if "medicalpolicy.bcbsok.com" in url.lower():
                skipped.append(
                    SkippedSource(
                        title=title,
                        url=url,
                        reason="BCBSOK terms-gated policy library. Direct HCSC PDFs are registered instead.",
                    )
                )
                continue
            payer_id = PAYER_IDS.get(payer_name)
            if payer_id is None:
                raise ValueError(f"no payer id for {payer_name}")
            access = _access(row)
            existing = _existing_id(row)
            dated = OFFICE_DATES.get(existing) if existing else None
            if dated is None:
                dated = parse_source_date(row.get("effective_or_review_date"))
            if existing:
                source_id = existing
                disposition = "already"
            else:
                source_id = _source_id(payer_id, number, title)
                disposition = "added"
            if source_id in seen:
                raise ValueError(f"duplicate source id {source_id}")
            seen.add(source_id)
            notes = row.get("access_notes") or ""
            unconfirmed = "unconfirmed" in notes.lower()
            fetch_block = blocked_fetch_reason(url)
            if access != "public":
                fetch = False
                fetch_reason = access
            elif dated.value is None:
                fetch = False
                fetch_reason = "no effective or review date to index"
            elif fetch_block:
                fetch = False
                fetch_reason = fetch_block
            elif disposition == "already":
                fetch = False
                fetch_reason = "already in the office corpus"
            else:
                fetch = True
                fetch_reason = "public"
            segment_ids = _segment_ids(payer_id, row.get("segment") or "", available[payer_id])
            links = [Link(payer_id, segment_id, "primary") for segment_id in segment_ids]
            if payer_id == "aetna" and access == "public":
                links = _fallback_links(number, links)
            state = "NA" if access == "not_applicable" else "OK"
            plan_type = "not-applicable" if access == "not_applicable" else "ok-payer"
            program = "not-oklahoma" if access == "not_applicable" else "oklahoma-payer"
            sources.append(
                PolicySource(
                    source_id=source_id,
                    payer_id=payer_id,
                    title=title,
                    number=number if number.lower() != "n/a" else "",
                    url=url,
                    topic=row.get("topic") or "",
                    topics=_topics(row.get("topic") or "", title),
                    date=dated,
                    access=access,
                    fetch=fetch,
                    fetch_reason=fetch_reason,
                    counts_as_coverage=_counts(
                        title,
                        row.get("topic") or "",
                        access,
                        dated.value is not None,
                        unconfirmed,
                    ),
                    unconfirmed=unconfirmed,
                    disposition=disposition,
                    publisher=(row.get("parent_company") or payer_name).strip(),
                    doc_type=_doc_type(title),
                    links=tuple(links),
                    state=state,
                    plan_type=plan_type,
                    program=program,
                )
            )
    return tuple(sources), tuple(skipped)


def sources() -> tuple[PolicySource, ...]:
    return inventory()[0]


def skipped_sources() -> tuple[SkippedSource, ...]:
    return inventory()[1]


def source_by_id(source_id: str) -> PolicySource | None:
    for item in sources():
        if item.source_id == source_id:
            return item
    return None


def added_sources() -> tuple[PolicySource, ...]:
    return tuple(item for item in sources() if item.disposition == "added")


def citable_sources() -> tuple[PolicySource, ...]:
    return tuple(item for item in sources() if item.citable)


def fetchable_sources() -> tuple[PolicySource, ...]:
    return tuple(item for item in sources() if item.fetch)


def linked_sources(payer_id: str, segment_id: str | None = None) -> tuple[PolicySource, ...]:
    found: list[PolicySource] = []
    for item in sources():
        if item.access == "not_applicable":
            continue
        for link in item.links:
            if link.payer_id != payer_id:
                continue
            if segment_id is not None and link.segment_id != segment_id:
                continue
            found.append(item)
            break
    return tuple(found)


def registry_counts() -> dict[str, int]:
    rows = sources()
    return {
        "csv_rows": len(rows) + len(skipped_sources()),
        "added": sum(item.disposition == "added" for item in rows),
        "already": sum(item.disposition == "already" for item in rows),
        "skipped": len(skipped_sources()),
        "citable": sum(item.citable for item in rows),
        "fetch": sum(item.fetch for item in rows),
        "pending": sum(item.access == PENDING_ACCESS for item in rows),
        "licensed": sum(item.access == "licensed" for item in rows),
        "login": sum(item.access == "login" for item in rows),
        "unverified": sum(item.access == "unverified" for item in rows),
        "not_applicable": sum(item.access == "not_applicable" for item in rows),
        "undated": sum(item.date.value is None for item in rows),
    }
