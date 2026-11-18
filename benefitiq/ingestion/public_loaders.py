"""Loaders for the public Oklahoma source set.

Marketplace and landscape rows are filtered to the manifest state. ICD-10-CM
descriptors from the CMS tabular file are public domain and are stored.
HCPCS Level II rows keep the code number and a placeholder label from
labels.yaml. Descriptor text from that file is discarded, including CDT text
on D-codes. CPT and other Level I codes are rejected and are not stored.
"""

from __future__ import annotations

import logging
import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from benefitiq.constants import FORBIDDEN_CONTENT_KEYS
from benefitiq.ingestion.loaders import (
    IngestionError,
    LoadedChunk,
    LoadedCode,
    LoadedDocument,
    SourceSpec,
    _check_code,
    register_loader,
)
from benefitiq.ingestion.tabular import TableReader
from benefitiq.labels import label_for, load_labels

logger = logging.getLogger("benefitiq.ingest")

STATE_KEYS = {"statecode", "state code", "state"}
LEVEL_II_RE = re.compile(r"^[A-Z][0-9]{4}$")
CPT_RE = re.compile(r"^[0-9]{4}[0-9A-Z]$")
ICD_CODE_RE = re.compile(r"^[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$")
_STATE_NAMES = {"OK": "OKLAHOMA"}


@dataclass(frozen=True)
class HcpcsParse:
    """Level II code numbers and rejected Level I codes. No descriptor text."""

    codes: list[str]
    rejected_cpt: list[str]


@register_loader("cms_marketplace_puf")
def load_cms_marketplace_puf(path: Path, spec: SourceSpec) -> LoadedDocument:
    return _load_state_table(path, spec, header_mode="first")


@register_loader("qhp_landscape")
def load_qhp_landscape(path: Path, spec: SourceSpec) -> LoadedDocument:
    return _load_state_table(path, spec, header_mode="state")


@register_loader("icd10cm_tabular")
def load_icd10cm_tabular(path: Path, spec: SourceSpec) -> LoadedDocument:
    loaded = LoadedDocument()
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for code, official_text in _iter_icd_codes(path):
        if code in seen:
            continue
        seen.add(code)
        found.append((code, official_text))
    scanned = len(found)
    for code, official_text in sorted(found):
        _check_code({"code_system": "ICD10CM", "code": code}, where=spec.source_id)
        anchor = code
        chunk_id = f"{spec.source_id}::{anchor}"
        fields = {
            "record_kind": "icd10cm_code",
            "code_system": "ICD10CM",
            "code": code,
            "official_text": official_text,
            "label": label_for(code) or "placeholder: unlabeled",
        }
        loaded.chunks.append(
            LoadedChunk(
                chunk_id=chunk_id,
                section_anchor=anchor,
                text=_record_text(fields),
                metadata=fields,
            )
        )
        loaded.code_rows.append(
            LoadedCode(
                code_row_id=chunk_id,
                chunk_id=chunk_id,
                code_system="ICD10CM",
                code=code,
                label_ref=code,
            )
        )
    loaded.source_rows = len(loaded.code_rows)
    loaded.scanned_rows = scanned
    _log_rows(spec, loaded)
    return loaded


@register_loader("hcpcs_anweb")
def load_hcpcs_anweb(path: Path, spec: SourceSpec) -> LoadedDocument:
    parsed = parse_hcpcs_anweb(_read_hcpcs_text(path))
    if parsed.rejected_cpt:
        preview = ", ".join(parsed.rejected_cpt[:20])
        raise IngestionError(
            "CPT/Level I codes are rejected and are not stored: " + preview
        )
    loaded = _hcpcs_document(parsed.codes, spec)
    loaded.scanned_rows = len(parsed.codes)
    _log_rows(spec, loaded)
    return loaded


def parse_hcpcs_anweb(text: str) -> HcpcsParse:
    """Split an ANWEB file into Level II code numbers and rejected CPT codes.

    Long and short description fields are read only so they can be discarded.
    They are not placed on the returned value.
    """
    codes: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    rejected_seen: set[str] = set()
    for line in text.splitlines():
        if len(line) < 11:
            continue
        padded = line.ljust(320)
        code = padded[0:5]
        record_type = padded[10]
        if record_type in {"4", "7", "8"}:
            continue
        if record_type != "3":
            raise IngestionError(f"unrecognized HCPCS record type {record_type!r}")
        # Positions 12-91 and 92-119 are the long and short descriptions.
        # Drop them for every code, including D-codes.
        _discarded_descriptors = (padded[11:91], padded[91:119])
        del _discarded_descriptors
        if CPT_RE.fullmatch(code):
            if code not in rejected_seen:
                rejected_seen.add(code)
                rejected.append(code)
            continue
        if LEVEL_II_RE.fullmatch(code):
            if code not in seen:
                seen.add(code)
                codes.append(code)
            continue
        raise IngestionError(f"unrecognized HCPCS procedure code {code!r}")
    return HcpcsParse(codes=codes, rejected_cpt=rejected)


def _hcpcs_document(codes: list[str], spec: SourceSpec) -> LoadedDocument:
    loaded = LoadedDocument()
    labels = load_labels()
    for code in codes:
        _check_code({"code_system": "HCPCS", "code": code}, where=spec.source_id)
        if code in labels:
            label = labels[code]
        else:
            label = "placeholder: unlabeled"
        if not label.startswith("placeholder:"):
            raise IngestionError(f"{spec.source_id} refused a non-placeholder label for {code}")
        chunk_id = f"{spec.source_id}::{code}"
        fields = {
            "record_kind": "hcpcs_code",
            "code_system": "HCPCS",
            "code": code,
            "label": label,
        }
        loaded.chunks.append(
            LoadedChunk(
                chunk_id=chunk_id,
                section_anchor=code,
                text=_record_text(fields),
                metadata=dict(fields),
            )
        )
        loaded.code_rows.append(
            LoadedCode(
                code_row_id=chunk_id,
                chunk_id=chunk_id,
                code_system="HCPCS",
                code=code,
                label_ref=code,
            )
        )
    loaded.source_rows = len(codes)
    return loaded


def _load_state_table(path: Path, spec: SourceSpec, *, header_mode: str) -> LoadedDocument:
    reader = TableReader(path, header_mode=header_mode)
    loaded = LoadedDocument()
    scanned = 0
    for row in reader:
        scanned += 1
        if not _matches_state(row, spec.state):
            continue
        fields = _public_fields(row)
        fields["record_kind"] = spec.doc_type
        if spec.state:
            fields["state"] = spec.state
        anchor = f"r{len(loaded.chunks) + 1:06d}"
        loaded.chunks.append(
            LoadedChunk(
                chunk_id=f"{spec.source_id}::{anchor}",
                section_anchor=anchor,
                text=_record_text(fields),
                metadata=fields,
            )
        )
    if spec.state and not any(header.strip().lower() in STATE_KEYS for header in reader.headers):
        raise IngestionError(f"{spec.source_id} has no state column to filter")
    loaded.source_rows = len(loaded.chunks)
    loaded.scanned_rows = scanned
    _log_rows(spec, loaded)
    return loaded


def _matches_state(row: dict[str, str], expected: str | None) -> bool:
    if not expected:
        return True
    cell = _state_cell(row)
    if cell is None:
        return False
    token = cell.strip().upper()
    wanted = expected.strip().upper()
    if token == wanted:
        return True
    full_name = _STATE_NAMES.get(wanted)
    return full_name is not None and token == full_name


def _state_cell(row: dict[str, str]) -> str | None:
    for key, value in row.items():
        if key.strip().lower() in STATE_KEYS:
            return value
    return None


def _public_fields(row: dict[str, str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in row.items():
        cleaned_key = key.strip()
        lowered = cleaned_key.lower()
        if lowered in FORBIDDEN_CONTENT_KEYS or "descriptor" in lowered:
            continue
        text = " ".join(value.split())
        if text:
            fields[cleaned_key] = text
    return fields


def _record_text(fields: dict[str, str]) -> str:
    return "\n".join(f"{key} {value}" for key, value in fields.items())


def _log_rows(spec: SourceSpec, loaded: LoadedDocument) -> None:
    logger.info(
        "public loader source_id=%s rows=%s scanned=%s chunks=%s",
        spec.source_id,
        loaded.source_rows,
        loaded.scanned_rows,
        len(loaded.chunks),
    )


def _read_hcpcs_text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        return path.read_bytes().decode("latin-1")
    if path.suffix.lower() != ".zip":
        raise IngestionError(f"{path} is not an HCPCS zip or text file")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise IngestionError(f"{path} is not a zip file") from exc
    with archive:
        names = [
            name
            for name in archive.namelist()
            if Path(name).name.lower().endswith(".txt")
            and "anweb" in Path(name).name.lower()
            and "proc_note" not in Path(name).name.lower()
            and "recordlayout" not in Path(name).name.lower()
        ]
        if len(names) != 1:
            raise IngestionError(f"{path} expected one ANWEB text file, found {names}")
        return archive.read(names[0]).decode("latin-1")


def _iter_icd_codes(path: Path) -> Iterator[tuple[str, str]]:
    with _tabular_handle(path) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "diag":
                continue
            code = ""
            official_text = ""
            for child in list(element):
                if child.tag == "name" and not code:
                    code = (child.text or "").strip()
                elif child.tag == "desc" and not official_text:
                    official_text = " ".join((child.text or "").split())
            element.clear()
            if code and official_text and ICD_CODE_RE.fullmatch(code):
                yield code, official_text


@contextmanager
def _tabular_handle(path: Path):
    if path.suffix.lower() == ".xml":
        with path.open("rb") as handle:
            yield handle
        return
    if path.suffix.lower() != ".zip":
        raise IngestionError(f"{path} is not an ICD-10-CM zip or xml file")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise IngestionError(f"{path} is not a zip file") from exc
    with archive:
        names = [
            name
            for name in archive.namelist()
            if Path(name).name.lower().endswith(".xml") and "tabular" in Path(name).name.lower()
        ]
        if len(names) != 1:
            raise IngestionError(f"{path} expected one tabular xml file, found {names}")
        with archive.open(names[0]) as handle:
            yield handle
