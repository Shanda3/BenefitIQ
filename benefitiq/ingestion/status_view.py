"""Plain-language version and status for the office screens."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from benefitiq.models import Source
from benefitiq.payers.catalog import PENDING_ACCESS, PolicySource

STATUS_CURRENT = "Current"
STATUS_REVIEW = "Needs review"
STATUS_BLOCKED = "Blocked"


def checked_words(value: datetime | None) -> str:
    if value is None:
        return "Not checked yet"
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def chip_for(label: str) -> str:
    if label == STATUS_CURRENT:
        return "chip-ready"
    if label == STATUS_REVIEW:
        return "chip-docs"
    if label == STATUS_BLOCKED:
        return "chip-none"
    return "chip-progress"


def _family_label(current: Source | None, waiting: list[Source]) -> str:
    if current is not None and current.check_state == "blocked":
        return STATUS_BLOCKED
    if waiting or (current is not None and current.status == "needs_review"):
        return STATUS_REVIEW
    if current is not None and current.check_state == "missing":
        return STATUS_REVIEW
    if current is not None and current.status == "current":
        return STATUS_CURRENT
    return "Not checked yet"


def version_index(session: Session) -> dict[str, dict]:
    """Map a catalog id to the version the screen should describe."""
    rows = list(session.scalars(select(Source).where(Source.doc_type == "policy_text")).all())
    grouped: dict[str, list[Source]] = {}
    for row in rows:
        grouped.setdefault(row.family_id, []).append(row)
    index: dict[str, dict] = {}
    for catalog_id, family in grouped.items():
        current = next((row for row in family if row.status == "current"), None)
        waiting = [row for row in family if row.status == "needs_review"]
        shown = current or (max(waiting, key=lambda row: row.version) if waiting else family[-1])
        label = _family_label(current, waiting)
        version = current.version if current is not None else shown.version
        if waiting and current is not None:
            version_words = f"Version {current.version} is cited. Version {max(row.version for row in waiting)} is waiting for review."
        elif label == STATUS_BLOCKED:
            version_words = f"Version {version} is blocked."
        else:
            version_words = f"Version {version}"
        index[catalog_id] = {
            "label": label,
            "chip": chip_for(label),
            "version": version,
            "version_words": version_words,
            "checked": checked_words(shown.last_checked_at or shown.retrieved_at),
            "source_id": shown.source_id,
        }
    return index


def catalog_status(source: PolicySource, info: dict | None) -> dict:
    """Status words for one registry row, including rows we do not download."""
    if info is not None:
        return info
    if source.access == PENDING_ACCESS:
        label = "Waiting on a disclaimer"
    elif source.access == "licensed":
        label = "Criteria aren't public"
    elif source.access == "login":
        label = "Login required"
    elif source.access == "unverified":
        label = "Web address not verified"
    elif source.access == "not_applicable":
        label = "Does not apply to Oklahoma"
    elif source.date.value is None:
        label = "No date on file"
    elif source.fetch:
        label = "Not checked yet"
    else:
        label = source.access_label
    return {
        "label": label,
        "chip": chip_for(label),
        "version": None,
        "version_words": "No version yet",
        "checked": "Not checked yet",
        "source_id": source.source_id,
    }


def library_status(row: Source) -> dict:
    """Status words for a stub or office row that is not a downloaded policy."""
    if row.check_state == "blocked":
        label = STATUS_BLOCKED
    elif row.status == "needs_review":
        label = STATUS_REVIEW
    elif row.status == "current":
        label = STATUS_CURRENT
    else:
        label = "Older version"
    return {
        "label": label,
        "chip": chip_for(label),
        "version": row.version,
        "version_words": f"Version {row.version}",
        "checked": checked_words(row.last_checked_at or row.retrieved_at),
    }
