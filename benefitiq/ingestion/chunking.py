"""Split policy text on headings, then on paragraph size.

HTML headings arrive as Markdown headings from the HTML extractor. PDF text is
split on short heading-like lines. A long section is split again so a chunk
stays readable. Each chunk keeps its heading.
"""

from __future__ import annotations

import re
from typing import Any

from benefitiq.ingestion.loaders import LoadedChunk

MAX_CHARS = 1600
# One public policy (Aetna CPB 0004) splits into about 600 heading windows.
# The cap stays above that so a yearly ingest keeps the whole file.
SAFETY_CAP = 800

_MD_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_NUMBERED = re.compile(r"^\d+(?:\.\d+){0,4}[.)]?\s+\S")
_KNOWN = re.compile(
    r"^(Policy|Coverage|Indications|Limitations|Description|Background|Criteria|"
    r"Exclusions|Documentation|Overview|Summary|Definitions)\b",
    re.IGNORECASE,
)


def _anchor(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "section")[:60]


def _heading_text(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return None
    match = _MD_HEADING.match(stripped)
    if match:
        title = match.group(1).strip()
        return title or None
    if stripped.endswith((".", ",", ";", ":")):
        return None
    if _NUMBERED.match(stripped) and len(stripped) <= 90:
        return stripped
    letters = [char for char in stripped if char.isalpha()]
    words = stripped.split()
    if letters and all(char.isupper() for char in letters) and 2 <= len(words) <= 12 and len(stripped) <= 80:
        return stripped
    if _KNOWN.match(stripped) and len(words) <= 8 and len(stripped) < 80:
        return stripped
    return None


def split_sections(text: str) -> list[tuple[str, str]]:
    """Return (heading, body) pairs. Body includes the heading line."""
    sections: list[tuple[str, list[str]]] = []
    heading = ""
    body: list[str] = []
    for line in text.splitlines():
        found = _heading_text(line)
        if found is not None and (body or heading):
            sections.append((heading, body))
            heading = found
            body = [line.strip()]
            continue
        if found is not None and not body and not heading:
            heading = found
            body = [line.strip()]
            continue
        body.append(line)
    sections.append((heading, body))
    rendered: list[tuple[str, str]] = []
    for title, lines in sections:
        content = "\n".join(lines).strip()
        if content:
            rendered.append((title, content))
    return rendered


def _windows(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [text]
    windows: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > limit:
            if current:
                windows.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                piece = paragraph[start : start + limit].strip()
                if piece:
                    windows.append(piece)
                start += limit
            continue
        if current and len(current) + 2 + len(paragraph) > limit:
            windows.append(current)
            current = paragraph
        elif current:
            current = f"{current}\n\n{paragraph}"
        else:
            current = paragraph
    if current:
        windows.append(current)
    return windows or [text[:limit]]


def chunk_policy_text(source_id: str, text: str, metadata: dict[str, Any] | None = None) -> list[LoadedChunk]:
    """Build citable chunks. `metadata` is copied onto every chunk."""
    base = dict(metadata or {})
    chunks: list[LoadedChunk] = []
    seen: set[str] = set()
    for heading, body in split_sections(text):
        anchor_base = _anchor(heading or "document")
        for index, piece in enumerate(_windows(body)):
            anchor = anchor_base if index == 0 else f"{anchor_base}-{index + 1}"
            unique = anchor
            suffix = 2
            while unique in seen:
                unique = f"{anchor}-{suffix}"
                suffix += 1
            seen.add(unique)
            meta = dict(base)
            meta["record_kind"] = "policy_text"
            meta["section"] = unique
            meta["heading"] = heading or "Document"
            chunks.append(
                LoadedChunk(
                    chunk_id=f"{source_id}::{unique}",
                    section_anchor=unique[:200],
                    text=piece,
                    metadata=meta,
                )
            )
            if len(chunks) >= SAFETY_CAP:
                return chunks
    if chunks:
        return chunks
    meta = dict(base)
    meta.update({"record_kind": "policy_text", "section": "indexed", "heading": "Document"})
    return [
        LoadedChunk(
            chunk_id=f"{source_id}::indexed",
            section_anchor="indexed",
            text="Indexed public document. Descriptor lines were omitted.",
            metadata=meta,
        )
    ]
