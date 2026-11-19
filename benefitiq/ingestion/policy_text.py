"""Turn a downloaded public policy into citable text.

Code-descriptor lines are dropped before anything is stored. CPT-looking lines
are removed. A CDT-looking line keeps the code number only.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from benefitiq.ingestion.loaders import LoadedChunk, LoadedDocument, SourceSpec, register_loader

_CPT_LINE = re.compile(r"(?<![A-Za-z0-9])\d{5}(?![A-Za-z0-9])")
_CDT = re.compile(r"\bD\d{4}\b", re.IGNORECASE)
_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_BREAK = re.compile(r"(?i)<br\s*/?>|</p>|</h[1-6]>|</li>|</tr>")
_HEAD = re.compile(r"(?i)<h[1-6][^>]*>")
_TAGS = re.compile(r"<[^>]+>")


def redact_policy_text(text: str) -> str:
    """Remove descriptor lines. Keep ordinary sentences."""
    kept: list[str] = []
    for line in text.splitlines():
        if _CPT_LINE.search(line):
            continue
        if _CDT.search(line):
            codes = " ".join(dict.fromkeys(code.upper() for code in _CDT.findall(line)))
            if codes:
                kept.append(codes)
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def html_to_text(raw: str) -> str:
    stripped = _TAG.sub(" ", raw)
    stripped = _HEAD.sub("\n## ", stripped)
    stripped = _BREAK.sub("\n", stripped)
    stripped = _TAGS.sub(" ", stripped)
    return html.unescape(stripped)


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


def _chunks(source_id: str, text: str) -> list[LoadedChunk]:
    blocks = re.split(r"\n(?=## )", text)
    chunks: list[LoadedChunk] = []
    for index, block in enumerate(blocks, start=1):
        body = block.strip()
        if not body:
            continue
        if len(body) > 4000:
            body = body[:4000].rsplit(" ", 1)[0]
        anchor = "indexed" if index == 1 else f"indexed-{index}"
        chunks.append(
            LoadedChunk(
                chunk_id=f"{source_id}::{anchor}",
                section_anchor=anchor,
                text=body,
                metadata={"record_kind": "policy_text", "section": anchor},
            )
        )
        if len(chunks) >= 40:
            break
    if not chunks:
        chunks.append(
            LoadedChunk(
                chunk_id=f"{source_id}::indexed",
                section_anchor="indexed",
                text="Indexed public document. Descriptor lines were omitted.",
                metadata={"record_kind": "policy_text", "section": "indexed"},
            )
        )
    return chunks


@register_loader("policy_text")
def load_policy_text(path: Path, spec: SourceSpec) -> LoadedDocument:
    payload = path.read_bytes()
    if payload.startswith(b"%PDF"):
        text = pdf_to_text(payload)
    else:
        text = html_to_text(payload.decode("utf-8", "replace"))
    redacted = redact_policy_text(text)
    if not redacted:
        redacted = "Indexed public document. Descriptor lines were omitted."
    return LoadedDocument(chunks=_chunks(spec.source_id, redacted), source_rows=1, scanned_rows=1)
