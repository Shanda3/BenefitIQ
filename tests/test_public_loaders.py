import re
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
import yaml
from sqlalchemy import delete, select

from benefitiq.db import session_scope
from benefitiq.ingestion.fetch import download
from benefitiq.ingestion.loaders import IngestionError, SourceSpec
from benefitiq.ingestion.public_loaders import (
    load_cms_marketplace_puf,
    load_hcpcs_anweb,
    load_icd10cm_tabular,
    load_qhp_landscape,
    parse_hcpcs_anweb,
)
from benefitiq.ingestion.robots import allows
from benefitiq.ingestion.service import ingest_manifest
from benefitiq.labels import load_labels
from benefitiq.models import Chunk, CodeRow, Source
from tests.conftest import ROOT

FIXTURES = ROOT / "corpus" / "public" / "fixtures"
PUBLIC_MANIFEST = ROOT / "corpus" / "public" / "manifest.yaml"
CMS_ROBOTS = """
User-agent: *
Allow: /core/*.css$
Disallow: /core/
Disallow: /*?
Disallow: /admin/
"""


def _spec(**overrides) -> SourceSpec:
    payload = {
        "source_id": "tmp-public",
        "family_id": "tmp-public",
        "title": "Temporary public source",
        "publisher": "Centers for Medicare & Medicaid Services",
        "url": "https://example.test/public",
        "effective_from": date(2027, 1, 1),
        "effective_to": date(2027, 12, 31),
        "doc_type": "plan_attributes",
        "loader": "cms_marketplace_puf",
        "payer": None,
        "state": "OK",
        "program": "marketplace",
        "plan_type": None,
    }
    payload.update(overrides)
    return SourceSpec(**payload)


def _texts(loaded) -> str:
    parts = [chunk.text for chunk in loaded.chunks]
    parts.extend(str(chunk.metadata) for chunk in loaded.chunks)
    parts.extend(row.code for row in loaded.code_rows)
    return "\n".join(parts)


def _delete_source(source_id: str) -> None:
    with session_scope() as session:
        session.execute(delete(CodeRow).where(CodeRow.source_id == source_id))
        session.execute(delete(Chunk).where(Chunk.source_id == source_id))
        session.execute(delete(Source).where(Source.source_id == source_id))


@pytest.mark.parametrize(
    ("filename", "kept_token", "dropped_token"),
    [
        ("plan_attributes.csv", "00000OK0000001", "00000TX0000001"),
        ("benefits.csv", "Sample benefit alpha", "Sample benefit beta"),
        ("rates.csv", "100.00", "200.00"),
        ("service_area.csv", "Alpha County", "Beta County"),
    ],
)
def test_cms_puf_loader_keeps_oklahoma_rows(filename, kept_token, dropped_token):
    loaded = load_cms_marketplace_puf(FIXTURES / filename, _spec(doc_type="plan_attributes"))
    text = _texts(loaded)
    expected_rows = 2 if filename == "plan_attributes.csv" else 1
    assert loaded.source_rows == expected_rows
    assert kept_token in text
    assert dropped_token not in text
    assert "SHOULD_NOT_STORE_DESCRIPTION" not in text
    assert all(chunk.metadata["state"] == "OK" for chunk in loaded.chunks)


def test_landscape_loader_keeps_oklahoma_rows():
    loaded = load_qhp_landscape(
        FIXTURES / "landscape_dental.csv",
        _spec(doc_type="landscape", loader="qhp_landscape"),
    )
    text = _texts(loaded)
    assert loaded.source_rows == 1
    assert loaded.scanned_rows == 2
    assert "00000OK0000001" in text
    assert "00000TX0000001" not in text
    assert "Example Issuer" in text


def test_landscape_xlsx_uses_the_state_header_after_the_preamble(tmp_path: Path):
    workbook = tmp_path / "landscape.xlsx"
    _write_xlsx(
        workbook,
        [
            ["12", "displayed records"],
            ["State Code", "County Name", "Plan ID (Standard Component)"],
            ["Oklahoma", "Alpha County", "00000OK0000001"],
            ["TX", "Beta County", "00000TX0000001"],
        ],
    )
    loaded = load_qhp_landscape(workbook, _spec(doc_type="landscape", loader="qhp_landscape"))
    assert loaded.source_rows == 1
    assert "00000OK0000001" in _texts(loaded)
    assert "00000TX0000001" not in _texts(loaded)


def test_state_filter_does_not_invent_oklahoma_rows(tmp_path: Path):
    document = tmp_path / "other.csv"
    document.write_text(
        "StateCode,PlanId\nTX,00000TX0000001\n",
        encoding="utf-8",
    )
    loaded = load_cms_marketplace_puf(document, _spec())
    assert loaded.source_rows == 0
    assert loaded.chunks == []
    assert loaded.scanned_rows == 1


def test_puf_zip_member_is_read(tmp_path: Path):
    archive_path = tmp_path / "plan-attributes-puf.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(FIXTURES / "plan_attributes.csv", "Plan_Attributes_PUF.csv")
    loaded = load_cms_marketplace_puf(archive_path, _spec())
    assert loaded.source_rows == 2
    assert "00000TX0000001" not in _texts(loaded)


def test_icd10cm_loader_stores_public_domain_descriptors():
    loaded = load_icd10cm_tabular(
        FIXTURES / "icd10cm_sample.xml",
        _spec(
            source_id="icd10cm-fy2027",
            doc_type="code_table",
            loader="icd10cm_tabular",
            state=None,
            program=None,
            effective_from=date(2026, 10, 1),
            effective_to=date(2027, 9, 30),
        ),
    )
    codes = [row.code for row in loaded.code_rows]
    assert codes == ["A00", "A00.0"]
    text = _texts(loaded)
    assert "Cholera" in text
    assert "Cholera due to Vibrio cholerae 01, biovar cholerae" in text
    assert "1" not in codes
    assert all(row.code_system == "ICD10CM" for row in loaded.code_rows)
    assert all(row.label_ref == row.code for row in loaded.code_rows)


def test_hcpcs_drops_d_code_descriptor_text_and_rejects_cpt(tmp_path: Path):
    fixture = (FIXTURES / "hcpcs_anweb.txt").read_text(encoding="ascii")
    assert "FIXTUREDCODETEXT" in fixture
    assert "FIXTURECPTTEXT" in fixture
    parsed = parse_hcpcs_anweb(fixture)
    dumped = repr(parsed)
    assert parsed.codes == ["D0140", "A0426"]
    assert parsed.rejected_cpt == ["99213", "0001U"]
    for token in (
        "FIXTUREDCODETEXT",
        "FIXTUREDSHORTX",
        "FIXTURELEVELIITEXT",
        "FIXTUREMODTEXT",
        "FIXTURECPTTEXT",
        "FIXTUREPLATEXT",
    ):
        assert token not in dumped

    spec = _spec(
        source_id="hcpcs-level-ii-2026-10",
        doc_type="code_table",
        loader="hcpcs_anweb",
        state=None,
        program=None,
        effective_from=date(2026, 10, 1),
        effective_to=date(2026, 12, 31),
    )
    with pytest.raises(IngestionError, match="CPT"):
        load_hcpcs_anweb(FIXTURES / "hcpcs_anweb.txt", spec)

    kept = "\n".join(
        line for line in fixture.splitlines() if line[:5] not in {"99213", "0001U"}
    )
    cleaned = tmp_path / "hcpcs.txt"
    cleaned.write_text(kept + "\n", encoding="ascii")
    loaded = load_hcpcs_anweb(cleaned, spec)
    text = _texts(loaded)
    assert [row.code for row in loaded.code_rows] == ["D0140", "A0426"]
    assert all(row.code_system == "HCPCS" for row in loaded.code_rows)
    assert "placeholder: exam-level-1" in text
    assert load_labels()["D0140"] == "placeholder: exam-level-1"
    assert "placeholder: unlabeled" in text
    assert "99213" not in text
    assert "0001U" not in text
    for token in (
        "FIXTUREDCODETEXT",
        "FIXTUREDSHORTX",
        "FIXTURELEVELIITEXT",
        "FIXTUREMODTEXT",
        "FIXTURECPTTEXT",
        "FIXTUREPLATEXT",
    ):
        assert token not in text


def test_public_manifest_registers_exact_sources_and_keeps_the_stub_separate():
    manifest = yaml.safe_load(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    by_id = {entry["source_id"]: entry for entry in manifest["documents"]}
    expected = {
        "cms-puf-py2027-plan-attributes": (
            "https://download.cms.gov/marketplace-puf/2027/plan-attributes-puf.zip",
            "2027-01-01",
            "2027-12-31",
            "cms_marketplace_puf",
            "OK",
        ),
        "cms-puf-py2027-benefits-cost-sharing": (
            "https://download.cms.gov/marketplace-puf/2027/benefits-and-cost-sharing-puf.zip",
            "2027-01-01",
            "2027-12-31",
            "cms_marketplace_puf",
            "OK",
        ),
        "cms-puf-py2027-rates": (
            "https://download.cms.gov/marketplace-puf/2027/rate-puf.zip",
            "2027-01-01",
            "2027-12-31",
            "cms_marketplace_puf",
            "OK",
        ),
        "cms-puf-py2027-service-area": (
            "https://download.cms.gov/marketplace-puf/2027/service-area-puf.zip",
            "2027-01-01",
            "2027-12-31",
            "cms_marketplace_puf",
            "OK",
        ),
        "qhp-landscape-py2027-individual-medical": (
            "https://data.healthcare.gov/datafile/py2027/individual_market_medical.zip",
            "2027-01-01",
            "2027-12-31",
            "qhp_landscape",
            "OK",
        ),
        "qhp-landscape-py2027-individual-dental": (
            "https://data.healthcare.gov/datafile/py2027/individual_market_dental.zip",
            "2027-01-01",
            "2027-12-31",
            "qhp_landscape",
            "OK",
        ),
        "qhp-landscape-py2027-shop-medical": (
            "https://data.healthcare.gov/datafile/py2027/shop_market_medical.zip",
            "2027-01-01",
            "2027-12-31",
            "qhp_landscape",
            "OK",
        ),
        "qhp-landscape-py2027-shop-dental": (
            "https://data.healthcare.gov/datafile/py2027/shop_market_dental.zip",
            "2027-01-01",
            "2027-12-31",
            "qhp_landscape",
            "OK",
        ),
        "icd10cm-fy2027": (
            "https://www.cms.gov/files/zip/2027-code-tables-tabular-index.zip",
            "2026-10-01",
            "2027-09-30",
            "icd10cm_tabular",
            None,
        ),
        "hcpcs-level-ii-2026-10": (
            "https://www.cms.gov/files/zip/october-2026-alpha-numeric-hcpcs-file.zip",
            "2026-10-01",
            "2026-12-31",
            "hcpcs_anweb",
            None,
        ),
        "hcpcs-level-ii-2027-01": (
            "https://www.cms.gov/files/zip/january-2027-alpha-numeric-hcpcs-file.zip",
            "2027-01-01",
            "2027-03-31",
            "hcpcs_anweb",
            None,
        ),
    }
    assert set(by_id) == set(expected)
    for source_id, (url, start, end, loader, state) in expected.items():
        entry = by_id[source_id]
        assert entry["url"] == url
        assert str(entry["effective_from"]) == start
        assert str(entry["effective_to"]) == end
        assert entry["loader"] == loader
        assert entry.get("state") == state
        assert entry["publisher"]
        assert entry["path"].startswith("cache/")
        assert "stub" not in entry["path"]
    october = by_id["hcpcs-level-ii-2026-10"]
    january = by_id["hcpcs-level-ii-2027-01"]
    assert october["source_id"] != january["source_id"]
    assert october["family_id"] == january["family_id"] == "hcpcs-level-ii"
    assert october["effective_to"] < january["effective_from"]
    stub = (ROOT / "corpus" / "stub" / "manifest.yaml").read_text(encoding="utf-8")
    assert "FICTIONAL STUB" in stub
    assert "https://download.cms.gov" not in stub


def test_public_source_records_provenance_and_refuses_overwrite(tmp_path: Path):
    source_id = "tmp-public-provenance"
    document = tmp_path / "rows.csv"
    document.write_text(
        "StateCode,PlanId\nOK,00000OK0000001\nTX,00000TX0000001\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "documents": [
                    {
                        "path": "rows.csv",
                        "source_id": source_id,
                        "family_id": "tmp-public-provenance",
                        "title": "Temporary Oklahoma plan attributes",
                        "publisher": "Centers for Medicare & Medicaid Services",
                        "url": "https://download.cms.gov/marketplace-puf/2027/plan-attributes-puf.zip",
                        "effective_from": "2027-01-01",
                        "effective_to": "2027-12-31",
                        "doc_type": "plan_attributes",
                        "loader": "cms_marketplace_puf",
                        "state": "OK",
                        "program": "marketplace",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    try:
        first = ingest_manifest(manifest)
        assert first["inserted"] == 1
        with session_scope() as session:
            stored = session.get(Source, source_id)
            assert stored is not None
            assert stored.url == "https://download.cms.gov/marketplace-puf/2027/plan-attributes-puf.zip"
            assert stored.publisher == "Centers for Medicare & Medicaid Services"
            assert stored.effective_from == date(2027, 1, 1)
            assert stored.effective_to == date(2027, 12, 31)
            assert stored.retrieved_at is not None
            assert re.fullmatch(r"[0-9a-f]{64}", stored.content_hash)
            assert stored.state == "OK"
            chunks = session.scalars(select(Chunk).where(Chunk.source_id == source_id)).all()
            assert len(chunks) == 1
            assert "00000OK0000001" in chunks[0].body
            assert "00000TX0000001" not in chunks[0].body
        again = ingest_manifest(manifest)
        assert again["skipped"] == 1
        document.write_text(document.read_text(encoding="utf-8") + "OK,00000OK0000009\n", encoding="utf-8")
        with pytest.raises(IngestionError, match="overwrite"):
            ingest_manifest(manifest)
    finally:
        _delete_source(source_id)


def test_robots_allows_cms_zip_and_blocks_query_strings():
    zip_url = "https://www.cms.gov/files/zip/october-2026-alpha-numeric-hcpcs-file.zip"
    assert allows(zip_url, CMS_ROBOTS)
    assert not allows(zip_url + "?download=1", CMS_ROBOTS)
    assert not allows("https://www.cms.gov/admin/content", CMS_ROBOTS)
    assert not allows("https://www.cms.gov/core/install.php", CMS_ROBOTS)


def test_download_refuses_a_disallowed_url(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "benefitiq.ingestion.fetch.fetch_robots",
        lambda host: "User-agent: *\nDisallow: /secret\n",
    )

    def fail_open(*_args, **_kwargs):
        raise AssertionError("disallowed URL was requested")

    monkeypatch.setattr("benefitiq.ingestion.fetch.urlopen", fail_open)
    with pytest.raises(IngestionError, match="robots.txt"):
        download("https://www.cms.gov/secret/file.zip", tmp_path / "file.zip", {})


def _write_xlsx(path: Path, rows: list[list[str]]) -> None:
    strings: list[str] = []
    index: dict[str, int] = {}

    def shared(value: str) -> int:
        if value not in index:
            index[value] = len(strings)
            strings.append(value)
        return index[value]

    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column_number, value in enumerate(row, start=1):
            column = chr(ord("A") + column_number - 1)
            cells.append(
                f'<c r="{column}{row_number}" t="s"><v>{shared(value)}</v></c>'
            )
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    shared_xml = "".join(f"<si><t>{escape(value)}</t></si>" for value in strings)
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>
""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Plans" sheetId="1" r:id="rId1"/></sheets>
</workbook>
""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>
""",
        "xl/sharedStrings.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{shared_xml}</sst>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(sheet_rows)
            + "</sheetData></worksheet>"
        ),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
