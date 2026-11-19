"""Write registry cards and a manifest the existing ingester can load.

Cards record the title, number, URL, date label, and access status. They do
not quote policy text. Raw downloads stay in the gitignored cache.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from benefitiq.payers.catalog import ROOT, citable_sources

CARDS = ROOT / "corpus" / "payers" / "cards"
MANIFEST = ROOT / "corpus" / "payers" / "manifest.yaml"


def card_text(source) -> str:
    number = source.number or "none"
    lines = [
        "## record",
        "",
        "Oklahoma source registry entry.",
        f"Document label: {number}",
        f"Topic: {source.topic}",
        f"Access: {source.access}",
        f"Date label: {source.date.label}",
        "This entry records the URL and the date. It does not quote the policy and it is not a coverage determination.",
    ]
    if source.access == "not_applicable":
        lines.append("This source does not apply to Oklahoma.")
    if source.unconfirmed:
        lines.append("Whether this title addresses the topic was not confirmed.")
    return "\n".join(lines) + "\n"


def manifest_documents() -> list[dict]:
    documents = []
    for source in citable_sources():
        documents.append(
            {
                "path": f"cards/{source.source_id}.md",
                "source_id": source.source_id,
                "family_id": source.source_id,
                "title": source.title,
                "publisher": source.publisher,
                "url": source.url,
                "effective_from": source.date.value.isoformat(),
                "doc_type": source.doc_type,
                "loader": "prose",
                "payer": source.payer_id,
                "state": source.state,
                "program": source.program,
                "plan_type": source.plan_type,
            }
        )
    return documents


def write_registry() -> dict[str, int]:
    CARDS.mkdir(parents=True, exist_ok=True)
    expected = set()
    for source in citable_sources():
        path = CARDS / f"{source.source_id}.md"
        path.write_text(card_text(source), encoding="utf-8")
        expected.add(path.name)
    for path in CARDS.glob("*.md"):
        if path.name not in expected:
            path.unlink()
    MANIFEST.write_text(
        "# Registry cards for Oklahoma payer sources.\n"
        "# Each card stores a URL and a date label. It is not a copy of the policy.\n"
        + yaml.safe_dump({"documents": manifest_documents()}, sort_keys=False),
        encoding="utf-8",
    )
    return {"cards": len(expected)}


def main() -> None:
    counts = write_registry()
    print(f"cards={counts['cards']}")


if __name__ == "__main__":
    main()
