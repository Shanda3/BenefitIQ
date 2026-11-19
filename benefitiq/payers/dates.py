"""Dates copied from the Oklahoma source list.

A clear effective date is stored as an effective date. When the list only has a
review, revision, or document date, that date is kept and labeled. A missing
date stays missing. Nothing here invents a day the list did not state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class SourceDate:
    """A date from the source list, plus the words that introduced it."""

    value: date | None
    basis: str
    label: str

    @property
    def iso(self) -> str | None:
        if self.value is None:
            return None
        return self.value.isoformat()


def _full_year(year: int) -> int:
    if year >= 100:
        return year
    return 2000 + year


def _mdy(month: int, day: int, year: int) -> date:
    return date(_full_year(year), month, day)


def _month_year(month: int, year: int) -> date:
    """Centene prints a month and a year. The day is the first of that month."""
    return date(_full_year(year), month, 1)


def _long_day(text: str) -> date | None:
    match = re.search(
        r"\b([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\b",
        text,
    )
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return _mdy(month, int(match.group(2)), int(match.group(3)))


def parse_source_date(raw: str | None) -> SourceDate:
    """Parse one `effective_or_review_date` cell from the source list."""
    text = (raw or "").strip()
    lowered = text.lower()
    if not text or lowered.startswith("not found"):
        return SourceDate(None, "not_found", "not found")
    if lowered.startswith("py") and "handbook" in lowered:
        year = re.search(r"20\d{2}", text)
        if year is None:
            return SourceDate(None, "not_found", "not found")
        value = date(int(year.group(0)), 1, 1)
        return SourceDate(
            value,
            "plan_year",
            f"{text} (plan year start; no day stated)",
        )

    policy = re.search(
        r"Policy Effective Date:?\s*(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if policy:
        value = _mdy(int(policy.group(1)), int(policy.group(2)), int(policy.group(3)))
        label = f"Policy Effective Date {value.strftime('%m/%d/%Y')}"
        # A second "Effective Date" on the same page is not the policy effective date.
        others = re.findall(r"(?<!Policy )Effective Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
        if others:
            label = f"{label}; the page also states Effective Date {others[0]}"
        return SourceDate(value, "effective", label)

    revision = re.search(
        r"Revision effective\s*(\d{4})-(\d{2})-(\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if revision:
        value = date(int(revision.group(1)), int(revision.group(2)), int(revision.group(3)))
        return SourceDate(value, "effective", "Revision effective")

    effective_long = re.search(
        r"Effective Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if effective_long:
        value = _long_day(effective_long.group(1))
        if value is not None:
            return SourceDate(value, "effective", "Effective Date")

    effective_slash = re.search(
        r"Effective Date:?\s*(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if effective_slash:
        value = _mdy(
            int(effective_slash.group(1)),
            int(effective_slash.group(2)),
            int(effective_slash.group(3)),
        )
        return SourceDate(value, "effective", "Effective Date")

    eff_iso = re.search(r"\beff\s+(\d{4})-(\d{2})-(\d{2})", text, flags=re.IGNORECASE)
    if eff_iso:
        value = date(int(eff_iso.group(1)), int(eff_iso.group(2)), int(eff_iso.group(3)))
        return SourceDate(value, "effective", "eff")

    bare_effective = re.search(
        r"(?<![\w])effective\s+(\d{4})-(\d{2})-(\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if bare_effective and "original effective" not in lowered:
        value = date(
            int(bare_effective.group(1)),
            int(bare_effective.group(2)),
            int(bare_effective.group(3)),
        )
        return SourceDate(value, "effective", "Effective")

    original = re.search(
        r"Original effective\s+(\d{4})-(\d{2})-(\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if original and revision is None:
        value = date(int(original.group(1)), int(original.group(2)), int(original.group(3)))
        return SourceDate(value, "effective", "Original effective")

    last_review = re.search(
        r"Last Review\s+(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if last_review:
        value = _mdy(int(last_review.group(1)), int(last_review.group(2)), int(last_review.group(3)))
        return SourceDate(value, "last_review", "Last Review")

    reviewed = re.search(
        r"Reviewed:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if reviewed:
        value = _long_day(reviewed.group(1))
        if value is not None:
            return SourceDate(value, "last_review", "Reviewed")

    last_revision = re.search(
        r"Date of Last Revision:\s*(\d{1,2})/(\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if last_revision:
        month = int(last_revision.group(1))
        year = int(last_revision.group(2))
        token = f"{last_revision.group(1)}/{last_revision.group(2)}"
        return SourceDate(
            _month_year(month, year),
            "last_review",
            f"Date of Last Revision (month and year only: {token})",
        )

    last_review_my = re.search(
        r"Last Review Date:\s*(\d{1,2})/(\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if last_review_my:
        token = f"{last_review_my.group(1)}/{last_review_my.group(2)}"
        return SourceDate(
            _month_year(int(last_review_my.group(1)), int(last_review_my.group(2))),
            "last_review",
            f"Last Review Date (month and year only: {token})",
        )

    revised_iso = re.search(
        r"\bRevised\s+(\d{4})-(\d{2})-(\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if revised_iso:
        value = date(int(revised_iso.group(1)), int(revised_iso.group(2)), int(revised_iso.group(3)))
        return SourceDate(value, "last_review", "Revised")

    revised_short = re.search(
        r"\bRevised\s+(\d{1,2})-(\d{1,2})-(\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if revised_short:
        value = _mdy(
            int(revised_short.group(1)),
            int(revised_short.group(2)),
            int(revised_short.group(3)),
        )
        return SourceDate(value, "last_review", "Revised")

    last_updated = re.search(
        r"(?:Last Updated|Page Last Modified):\s*(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if last_updated:
        value = _mdy(
            int(last_updated.group(1)),
            int(last_updated.group(2)),
            int(last_updated.group(3)),
        )
        kind = "Last Updated" if "last updated" in lowered else "Page Last Modified"
        return SourceDate(value, "last_updated", kind)

    if lowered.startswith("date:"):
        value = _long_day(text)
        if value is not None:
            return SourceDate(
                value,
                "document_date",
                "Date (not labeled effective or last review)",
            )

    section = re.search(r"effective\s+(\d{4})-(\d{2})-(\d{2})", lowered)
    if section:
        value = date(int(section.group(1)), int(section.group(2)), int(section.group(3)))
        return SourceDate(value, "effective", "effective")

    return SourceDate(None, "not_found", "not found")
