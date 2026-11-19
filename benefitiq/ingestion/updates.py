"""Re-fetch eligible payer policies and compare them with the version on file.

A changed file is stored as a new version that waits for review. The current
version stays in place. The report is plain language plus a CSV. It does not
quote policy text.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from benefitiq.db import session_scope
from benefitiq.ingestion.payer_fetch import FetchView, fetch_document
from benefitiq.ingestion.policies import (
    content_hash,
    eligible,
    ingest_policy_bytes,
    load_ledger,
    mark_check_state,
    stated_date,
    touch_checked,
    write_ledger,
)
from benefitiq.ingestion.policy_text import extract_text, redact_policy_text
from benefitiq.payers.catalog import PENDING_ACCESS, PolicySource, sources

REPORT_DIR = Path("reports")


@dataclass
class UpdateRow:
    source_id: str
    payer: str
    title: str
    url: str
    result: str
    note: str
    version: str
    previous_hash: str
    new_hash: str
    previous_date: str
    new_date: str


def classify_fetch(
    *,
    fetch_kind: str,
    previous_hash: str | None,
    new_hash: str | None,
    previous_date: date | None,
    new_date: date | None,
) -> str:
    """Name the outcome of one re-fetch. Hashes and dates are compared here."""
    if fetch_kind == "blocked":
        return "blocked"
    if fetch_kind == "not_found":
        return "not_found"
    if fetch_kind == "moved":
        return "moved"
    if fetch_kind != "ok":
        return "error"
    if not previous_hash:
        return "new"
    if new_hash and new_hash != previous_hash:
        return "changed"
    if new_date is not None and previous_date is not None and new_date != previous_date:
        return "new_date"
    return "unchanged"


_NOTES = {
    "unchanged": "The file matches the version on file.",
    "changed": "The file changed. The new version is waiting for a person to review it.",
    "new_date": "The date in the file changed. The new version is waiting for a person to review it.",
    "not_found": "The page was not found.",
    "moved": "The page moved.",
    "blocked": "The site's robots.txt or terms now block a download.",
    "error": "The check could not finish for this file.",
    "new": "This file is not in the library yet. It is waiting for review.",
    "pending": "Still waiting. This file is not downloaded.",
}


def _iso(value: date | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _pending_note(source: PolicySource) -> str:
    if source.access == PENDING_ACCESS or "humana" in source.payer_id:
        return "Still waiting on a disclaimer. This file is not downloaded."
    if source.date.value is None:
        return "Still waiting. No effective or review date is on file, so this file is not downloaded."
    if source.access == "licensed":
        return "Still waiting. This payer's criteria are not public."
    if source.access == "login":
        return "Still waiting. The file is behind a login."
    if source.access == "unverified":
        return "Still waiting. The web address is not verified."
    if source.access == "not_applicable":
        return "Still waiting. This source does not apply to Oklahoma."
    return "Still waiting. This file is not downloaded."


def check_updates(
    *,
    write_db: bool = True,
    report_dir: Path = REPORT_DIR,
    fetcher=fetch_document,
    catalog: list[PolicySource] | None = None,
) -> list[UpdateRow]:
    """Re-fetch eligible sources, update review state, and write the report."""
    rows_in = list(catalog) if catalog is not None else list(sources())
    ledger = load_ledger()
    found: list[UpdateRow] = []
    when = datetime.now(timezone.utc)

    def _run(session) -> None:
        for source in rows_in:
            if not eligible(source):
                found.append(
                    UpdateRow(
                        source_id=source.source_id,
                        payer=source.payer_id,
                        title=source.title,
                        url=source.url,
                        result="pending",
                        note=_pending_note(source),
                        version="",
                        previous_hash="",
                        new_hash="",
                        previous_date=_iso(source.date.value),
                        new_date="",
                    )
                )
                continue
            fetched: FetchView = fetcher(source.url)
            previous = ledger.get(source.source_id) or {}
            previous_hash = previous.get("content_hash") or None
            previous_date = _parse_iso(previous.get("stated_on") or previous.get("effective_from"))
            new_hash = content_hash(fetched.payload) if fetched.payload else None
            new_stated: date | None = None
            if fetched.kind == "ok" and fetched.payload:
                try:
                    redacted = redact_policy_text(extract_text(fetched.payload))
                except Exception:  # noqa: BLE001 - a bad file is an error outcome, not a crash
                    redacted = ""
                parsed = stated_date(redacted) if redacted else None
                new_stated = parsed.value if parsed else None
            label = classify_fetch(
                fetch_kind=fetched.kind,
                previous_hash=previous_hash,
                new_hash=new_hash,
                previous_date=previous_date,
                new_date=new_stated,
            )
            note = _NOTES[label]
            if label == "error" and fetched.detail:
                note = "The check could not finish for this file."
            if write_db and session is not None and fetched.payload and label in {"changed", "new_date", "new"}:
                try:
                    ingest_policy_bytes(
                        session,
                        source,
                        fetched.payload,
                        make_current=False,
                        retrieved_at=when,
                    )
                except Exception:  # noqa: BLE001 - one file must not stop the report
                    label = "error"
                    note = "The check could not store the new version."
            elif write_db and session is not None and label == "unchanged":
                touch_checked(session, source.source_id)
            elif write_db and session is not None and label == "blocked":
                mark_check_state(session, source.source_id, "blocked")
            elif write_db and session is not None and label in {"not_found", "moved"}:
                mark_check_state(session, source.source_id, "missing")
            version = str(previous.get("version") or "")
            if label in {"changed", "new_date", "new"} and previous.get("version"):
                version = str(int(previous["version"]) + 1)
            found.append(
                UpdateRow(
                    source_id=source.source_id,
                    payer=source.payer_id,
                    title=source.title,
                    url=source.url,
                    result=label,
                    note=note,
                    version=version,
                    previous_hash=previous_hash or "",
                    new_hash=new_hash or "",
                    previous_date=_iso(previous_date),
                    new_date=_iso(new_stated),
                )
            )
        if write_db and session is not None:
            write_ledger(session)

    if write_db:
        with session_scope() as session:
            _run(session)
    else:
        _run(None)
    write_report(found, report_dir)
    return found


def write_report(rows: list[UpdateRow], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown = report_dir / "policy-updates.md"
    table = report_dir / "policy-updates.csv"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.result] = counts.get(row.result, 0) + 1
    order = (
        ("unchanged", "Unchanged"),
        ("changed", "Changed, needs review"),
        ("new_date", "New date, needs review"),
        ("new", "Not in the library yet"),
        ("not_found", "Page not found"),
        ("moved", "Page moved"),
        ("blocked", "Robots or terms now blocking"),
        ("error", "Could not check"),
        ("pending", "Still pending"),
    )
    lines = [
        "# Payer policy update check",
        "",
        "Decision support for a human biller. This report does not change what the office tool cites.",
        "A changed file stays in review until a person approves it.",
        "",
        "## Counts",
        "",
    ]
    for key, title in order:
        lines.append(f"- {title}: {counts.get(key, 0)}")
    lines.append("")
    for key, title in order:
        group = [row for row in rows if row.result == key]
        lines.append(f"## {title}")
        lines.append("")
        if not group:
            lines.append("None.")
            lines.append("")
            continue
        for row in group:
            lines.append(f"- **{row.title}** ({row.payer}). {row.note}")
            if row.previous_date or row.new_date:
                lines.append(f"  - Date on file: {row.previous_date or 'none'}. Date seen now: {row.new_date or 'none'}.")
            lines.append(f"  - Address: {row.url}")
        lines.append("")
    markdown.write_text("\n".join(lines), encoding="utf-8")
    with table.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_id",
                "payer",
                "title",
                "url",
                "result",
                "note",
                "version",
                "previous_hash",
                "new_hash",
                "previous_date",
                "new_date",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.source_id,
                    row.payer,
                    row.title,
                    row.url,
                    row.result,
                    row.note,
                    row.version,
                    row.previous_hash,
                    row.new_hash,
                    row.previous_date,
                    row.new_date,
                ]
            )
