"""Stream CSV and XLSX public-use tables.

Landscape workbooks start with a preamble row. The header is the first row
that names a state column. Marketplace PUF files use the first row.
"""

from __future__ import annotations

import csv
import io
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

from benefitiq.ingestion.loaders import IngestionError

_STATE_HEADERS = {"state code", "statecode"}


class TableReader:
    """Iterate data rows. ``headers`` is set once iteration starts."""

    def __init__(self, path: Path, *, header_mode: str) -> None:
        if header_mode not in {"first", "state"}:
            raise IngestionError(f"unknown header mode '{header_mode}'")
        self.path = path
        self.header_mode = header_mode
        self.headers: list[str] = []

    def __iter__(self) -> Iterator[dict[str, str]]:
        suffix = self.path.suffix.lower()
        if suffix == ".csv":
            with self.path.open("rb") as handle:
                yield from self._rows_from_lists(_csv_lists(handle))
            return
        if suffix == ".xlsx":
            yield from self._rows_from_lists(_xlsx_lists(self.path))
            return
        if suffix == ".zip":
            yield from self._rows_from_zip()
            return
        raise IngestionError(f"{self.path} is not a csv, xlsx, or zip table")

    def _rows_from_zip(self) -> Iterator[dict[str, str]]:
        try:
            archive = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise IngestionError(f"{self.path} is not a zip file") from exc
        with archive:
            members = [
                info
                for info in archive.infolist()
                if not info.is_dir() and not info.filename.startswith("__MACOSX")
            ]
            csv_members = [info for info in members if info.filename.lower().endswith(".csv")]
            xlsx_members = [info for info in members if info.filename.lower().endswith(".xlsx")]
            if len(csv_members) == 1 and not xlsx_members:
                with archive.open(csv_members[0]) as handle:
                    yield from self._rows_from_lists(_csv_lists(handle))
                return
            if len(xlsx_members) == 1 and not csv_members:
                with tempfile.NamedTemporaryFile(suffix=".xlsx") as temporary:
                    with archive.open(xlsx_members[0]) as handle:
                        shutil.copyfileobj(handle, temporary)
                    temporary.flush()
                    yield from self._rows_from_lists(_xlsx_lists(Path(temporary.name)))
                return
        raise IngestionError(f"{self.path} must contain one csv or one xlsx table")

    def _rows_from_lists(self, rows: Iterator[list[str]]) -> Iterator[dict[str, str]]:
        header: list[str] | None = None
        for cells in rows:
            if not any(cell.strip() for cell in cells):
                continue
            if header is None:
                if self.header_mode == "state" and not _looks_like_state_header(cells):
                    continue
                header = _clean_header(cells)
                self.headers = header
                if not any(header):
                    raise IngestionError(f"{self.path} header row is empty")
                continue
            yield {key: value for key, value in zip(header, cells, strict=False) if key}
        if header is None:
            raise IngestionError(f"{self.path} has no header row")


def _looks_like_state_header(cells: list[str]) -> bool:
    return any(cell.strip().lower() in _STATE_HEADERS for cell in cells)


def _clean_header(cells: list[str]) -> list[str]:
    return [cell.strip() for cell in cells]


def _csv_lists(handle) -> Iterator[list[str]]:
    text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
    for row in csv.reader(text):
        yield [cell.strip() for cell in row]


def _xlsx_lists(path: Path) -> Iterator[list[str]]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise IngestionError(f"{path} is not an xlsx workbook") from exc
    with archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as handle:
                for _event, element in ET.iterparse(handle, events=("end",)):
                    if element.tag.endswith("}si"):
                        shared.append(
                            "".join(
                                node.text or ""
                                for node in element.iter()
                                if node.tag.endswith("}t")
                            )
                        )
                        element.clear()
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in archive.namelist():
            raise IngestionError(f"{path} has no worksheet")
        with archive.open(sheet_name) as handle:
            for _event, element in ET.iterparse(handle, events=("end",)):
                if not element.tag.endswith("}row"):
                    continue
                indexed: list[tuple[int, str]] = []
                for cell in list(element):
                    if not cell.tag.endswith("}c"):
                        continue
                    column = "".join(char for char in cell.attrib.get("r", "") if char.isalpha())
                    if not column:
                        cell.clear()
                        continue
                    kind = cell.attrib.get("t")
                    value = ""
                    raw_value = None
                    for child in list(cell):
                        if child.tag.endswith("}v"):
                            raw_value = child.text
                        elif child.tag.endswith("}is"):
                            value = "".join(
                                node.text or ""
                                for node in child.iter()
                                if node.tag.endswith("}t")
                            )
                    if kind == "s" and raw_value is not None:
                        value = shared[int(raw_value)]
                    elif raw_value is not None and not value:
                        value = raw_value
                    indexed.append((_column_index(column), value.strip()))
                    cell.clear()
                element.clear()
                if not indexed:
                    continue
                width = max(index for index, _value in indexed)
                row = [""] * width
                for index, value in indexed:
                    row[index - 1] = value
                yield row


def _column_index(column: str) -> int:
    number = 0
    for char in column:
        number = number * 26 + (ord(char.upper()) - 64)
    return number
