"""Loaders turn a manifest entry into citable chunks.

Register a new loader with ``register_loader`` and name it from the manifest.
Structured loaders reject descriptor fields so copyrighted CDT/CPT wording
cannot be stored as a column.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from benefitiq.constants import CODE_SYSTEMS, FORBIDDEN_CONTENT_KEYS
from benefitiq.labels import load_labels

Loader = Callable[[Path, "SourceSpec"], "LoadedDocument"]
LOADER_REGISTRY: dict[str, Loader] = {}


class IngestionError(ValueError):
    """The document cannot be ingested as written."""


@dataclass
class SourceSpec:
    source_id: str
    family_id: str
    title: str
    publisher: str
    url: str
    effective_from: Any
    effective_to: Any
    doc_type: str
    loader: str
    payer: str | None
    state: str | None
    program: str | None
    plan_type: str | None


@dataclass
class LoadedChunk:
    chunk_id: str
    section_anchor: str
    text: str
    metadata: dict[str, Any]


@dataclass
class LoadedCode:
    code_row_id: str
    chunk_id: str
    code_system: str
    code: str
    label_ref: str


@dataclass
class LoadedDocument:
    chunks: list[LoadedChunk] = field(default_factory=list)
    code_rows: list[LoadedCode] = field(default_factory=list)
    # Rows kept after a state filter. Code tables report the codes stored.
    source_rows: int = 0
    scanned_rows: int = 0
    rejected_codes: list[str] = field(default_factory=list)


def register_loader(name: str) -> Callable[[Loader], Loader]:
    def decorator(func: Loader) -> Loader:
        LOADER_REGISTRY[name] = func
        return func

    return decorator


def get_loader(name: str) -> Loader:
    if name not in LOADER_REGISTRY:
        # Importing registers the public-source loaders without a cycle at module import.
        import benefitiq.ingestion.public_loaders  # noqa: F401

    try:
        return LOADER_REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(LOADER_REGISTRY)) or "(none)"
        raise IngestionError(f"unknown loader '{name}'. Registered loaders: {known}") from exc


def _reject_forbidden_keys(value: Any, *, where: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_CONTENT_KEYS:
                raise IngestionError(
                    f"{where} contains forbidden field '{key}'. "
                    "Store code numbers and placeholder label refs only."
                )
            _reject_forbidden_keys(child, where=where)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_keys(child, where=where)


def _anchor(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _as_str_list(value: Any, *, field_name: str, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise IngestionError(f"{where} field '{field_name}' must be a list of strings")
    return list(value)


def _chunk_text(record_kind: str, row_id: str, payload: dict[str, Any]) -> str:
    lines = ["FICTIONAL STUB" if "FICTIONAL STUB" not in str(payload) else "STUB RECORD", f"record_kind {record_kind}", f"id {row_id}"]
    # Always lead with the stub marker so the corpus scan can find it in generated text too.
    lines[0] = "FICTIONAL STUB"
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, list):
            rendered = " ".join(str(item) for item in value)
        else:
            rendered = str(value)
        lines.append(f"{key} {rendered}")
    return "\n".join(lines)


@register_loader("prose")
def load_prose(path: Path, spec: SourceSpec) -> LoadedDocument:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise IngestionError(f"{path} is empty")
    lines = raw.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_anchor = "intro"
    current: list[str] = []
    for line in lines:
        if line.startswith("## "):
            sections.append((current_anchor, current))
            current_anchor = _anchor(line[3:].strip())
            current = [line]
        else:
            current.append(line)
    sections.append((current_anchor, current))

    loaded = LoadedDocument()
    seen: set[str] = set()
    for anchor, content_lines in sections:
        text = "\n".join(content_lines).strip()
        if not text:
            continue
        unique = anchor
        suffix = 2
        while unique in seen:
            unique = f"{anchor}-{suffix}"
            suffix += 1
        seen.add(unique)
        loaded.chunks.append(
            LoadedChunk(
                chunk_id=f"{spec.source_id}::{unique}",
                section_anchor=unique,
                text=text,
                metadata={"record_kind": "prose", "section": unique},
            )
        )
    if not loaded.chunks:
        raise IngestionError(f"{path} did not produce any prose chunks")
    return loaded


def _require_id(row: dict[str, Any], key: str, *, where: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IngestionError(f"{where} is missing string id field '{key}'")
    return value.strip()


def _check_code(row: dict[str, Any], *, where: str) -> None:
    system = row.get("code_system")
    code = row.get("code")
    if system not in CODE_SYSTEMS:
        raise IngestionError(f"{where} has unsupported code_system '{system}'")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9.]{3,16}", code):
        raise IngestionError(f"{where} has an invalid code number")


@register_loader("structured")
def load_structured(path: Path, spec: SourceSpec) -> LoadedDocument:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise IngestionError(f"{path} must be a mapping")
    _reject_forbidden_keys(raw, where=str(path))
    loaded = LoadedDocument()

    collections = {
        "coding_rules": ("coding_rule", "rule_id"),
        "coverage_rules": ("coverage_rule", "rule_id"),
        "routing_rules": ("routing_rule", "route_id"),
        "checklist": ("checklist", "item_id"),
        "bundle_rules": ("bundle_rule", "bundle_id"),
    }
    for collection, (record_kind, id_key) in collections.items():
        rows = raw.get(collection) or []
        if not isinstance(rows, list):
            raise IngestionError(f"{path} field '{collection}' must be a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise IngestionError(f"{path} {collection}[{index}] must be a mapping")
            row_id = _require_id(row, id_key, where=f"{path} {collection}[{index}]")
            if record_kind in {"coding_rule", "coverage_rule"}:
                _check_code(row, where=f"{path} {row_id}")
            if record_kind == "coverage_rule" and not spec.payer:
                raise IngestionError(
                    f"{path} {row_id} is a coverage rule and the source must name a payer"
                )
            if record_kind == "bundle_rule":
                codes = _as_str_list(row.get("codes"), field_name="codes", where=f"{path} {row_id}")
                if len(codes) < 2:
                    raise IngestionError(f"{path} {row_id} bundle must list at least two codes")
                if not spec.payer:
                    raise IngestionError(f"{path} {row_id} bundle rule requires a payer")
            requires = row.get("requires")
            if requires is not None:
                _as_str_list(requires, field_name="requires", where=f"{path} {row_id}")
            scenario = row.get("scenario")
            if not isinstance(scenario, str) or not scenario.strip():
                raise IngestionError(f"{path} {row_id} must name a scenario")
            metadata = {"record_kind": record_kind, **row}
            loaded.chunks.append(
                LoadedChunk(
                    chunk_id=f"{spec.source_id}::{row_id}",
                    section_anchor=row_id,
                    text=_chunk_text(record_kind, row_id, metadata),
                    metadata=metadata,
                )
            )

    labels = load_labels()
    for index, row in enumerate(raw.get("code_rows") or []):
        if not isinstance(row, dict):
            raise IngestionError(f"{path} code_rows[{index}] must be a mapping")
        _check_code(row, where=f"{path} code_rows[{index}]")
        row_id = _require_id(row, "code_row_id", where=f"{path} code_rows[{index}]")
        label_ref = row.get("label_ref")
        if not isinstance(label_ref, str) or label_ref not in labels:
            raise IngestionError(
                f"{path} {row_id} label_ref must exist in labels.yaml"
            )
        if label_ref != row["code"]:
            raise IngestionError(f"{path} {row_id} label_ref must match the code number")
        metadata = {
            "record_kind": "code_row",
            "code_system": row["code_system"],
            "code": row["code"],
            "label_ref": label_ref,
        }
        chunk_id = f"{spec.source_id}::{row_id}"
        loaded.chunks.append(
            LoadedChunk(
                chunk_id=chunk_id,
                section_anchor=row_id,
                text=_chunk_text("code_row", row_id, metadata),
                metadata=metadata,
            )
        )
        loaded.code_rows.append(
            LoadedCode(
                code_row_id=f"{spec.source_id}::{row_id}",
                chunk_id=chunk_id,
                code_system=row["code_system"],
                code=row["code"],
                label_ref=label_ref,
            )
        )

    if not loaded.chunks:
        raise IngestionError(f"{path} did not contain any structured rows")
    return loaded
