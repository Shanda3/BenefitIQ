"""Turn a downloaded public policy into citable text.

Code-descriptor lines are dropped before anything is stored. CPT-looking lines
are removed. A CDT-looking line keeps the code number only.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from benefitiq.ingestion.chunking import chunk_policy_text
from benefitiq.ingestion.loaders import LoadedDocument, SourceSpec, register_loader

_CPT_LINE = re.compile(r"(?<![A-Za-z0-9])\d{5}(?![A-Za-z0-9])")
_CDT = re.compile(r"\bD\d{4}\b", re.IGNORECASE)
_HCPCS = re.compile(r"\b[A-Z]\d{4}\b", re.IGNORECASE)
_CODE_ONLY = re.compile(
    r"(?:D\d{4}|\d{5}|[A-Z]\d{4})(?:[,\s]+(?:D\d{4}|\d{5}|[A-Z]\d{4}))*",
    re.IGNORECASE,
)
_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_BREAK = re.compile(r"(?i)<br\s*/?>|</p>|</h[1-6]>|</li>|</tr>")
_HEAD = re.compile(r"(?i)<h[1-6][^>]*>")
_TAGS = re.compile(r"<[^>]+>")


def _kept_codes(stripped: str) -> str:
    """Code numbers that may be shown. CPT numbers are omitted."""
    cdt = [code.upper() for code in _CDT.findall(stripped)]
    hcpcs = [
        code.upper()
        for code in _HCPCS.findall(stripped)
        if not code.upper().startswith("D")
    ]
    return " ".join(dict.fromkeys(cdt + hcpcs))


def redact_policy_text(text: str) -> str:
    """Remove descriptor lines. Keep ordinary sentences and code numbers.

    A table often puts the code on its own line and the descriptor on the
    next indented line. Both are dropped from anything the screen can quote.
    CPT numbers are omitted. A CDT or HCPCS number stays, without the words
    that followed it.
    """
    raw_lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index]
        stripped = line.strip()
        if stripped and _CODE_ONLY.fullmatch(stripped):
            codes = _kept_codes(stripped)
            if codes:
                kept.append(codes)
            index += 1
            while index < len(raw_lines) and raw_lines[index][:1].isspace() and raw_lines[index].strip():
                index += 1
            continue
        if _CPT_LINE.search(line):
            index += 1
            continue
        if _CDT.search(line):
            codes = _kept_codes(stripped)
            if codes:
                kept.append(codes)
            index += 1
            continue
        kept.append(line.rstrip())
        index += 1
    return "\n".join(kept).strip()


def html_to_text(raw: str) -> str:
    stripped = _TAG.sub(" ", raw)
    stripped = _HEAD.sub("\n## ", stripped)
    stripped = _BREAK.sub("\n", stripped)
    stripped = _TAGS.sub(" ", stripped)
    return html.unescape(stripped)


def excerpt(text: str, limit: int = 280) -> str:
    """Short quote for the screen. Descriptor lines are removed first."""
    cleaned = redact_policy_text(text or "")
    flat = " ".join(cleaned.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def extract_text(payload: bytes) -> str:
    if payload.startswith(b"%PDF"):
        return pdf_to_text(payload)
    return html_to_text(payload.decode("utf-8", "replace"))


def pdf_to_text(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is installed with requirements
        raise RuntimeError("pypdf is required to index a PDF policy") from exc
    from io import BytesIO

    reader = PdfReader(BytesIO(payload))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


@register_loader("policy_text")
def load_policy_text(path: Path, spec: SourceSpec) -> LoadedDocument:
    redacted = redact_policy_text(extract_text(path.read_bytes()))
    if not redacted:
        redacted = "Indexed public document. Descriptor lines were omitted."
    return LoadedDocument(
        chunks=chunk_policy_text(
            spec.source_id,
            redacted,
            {"payer": spec.payer, "url": spec.url},
        ),
        source_rows=1,
        scanned_rows=1,
    )
