from pathlib import Path

import pytest
import yaml
from sqlalchemy import delete, select

from benefitiq.db import session_scope
from benefitiq.ingestion.loaders import IngestionError
from benefitiq.ingestion.service import ingest_manifest, load_manifest
from benefitiq.models import Chunk, CodeRow, Source
from tests.conftest import MANIFEST


def _delete_source(source_id: str) -> None:
    with session_scope() as session:
        session.execute(delete(CodeRow).where(CodeRow.source_id == source_id))
        session.execute(delete(Chunk).where(Chunk.source_id == source_id))
        session.execute(delete(Source).where(Source.source_id == source_id))


def test_stub_manifest_is_idempotent():
    first = ingest_manifest(MANIFEST)
    assert first["inserted"] == 0
    assert first["skipped"] > 0


def test_missing_url_is_rejected(tmp_path: Path):
    document = tmp_path / "note.md"
    document.write_text("# FICTIONAL STUB\n\n## body\n\nSynthetic text.\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "documents": [
                    {
                        "path": "note.md",
                        "source_id": "tmp-missing-url",
                        "family_id": "tmp-missing-url",
                        "title": "Missing url (FICTIONAL STUB)",
                        "publisher": "BenefitIQ Stub Corpus",
                        "effective_from": "2024-01-01",
                        "doc_type": "policy",
                        "loader": "prose",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IngestionError):
        load_manifest(manifest)


def test_descriptor_field_is_rejected_and_overwrite_is_refused(tmp_path: Path):
    source_id = "tmp-structured-source"
    document = tmp_path / "rows.yaml"
    manifest = tmp_path / "manifest.yaml"

    def write_rows(extra=None):
        row = {
            "rule_id": "tmp-rule",
            "scenario": "tmp_scenario",
            "pathway": "dental",
            "code_system": "CDT",
            "code": "D1110",
            "requires": ["prophylaxis_done"],
            "volunteer": False,
        }
        if extra:
            row.update(extra)
        document.write_text(
            yaml.safe_dump({"stub": "FICTIONAL STUB", "coding_rules": [row]}),
            encoding="utf-8",
        )
        manifest.write_text(
            yaml.safe_dump(
                {
                    "documents": [
                        {
                            "path": "rows.yaml",
                            "source_id": source_id,
                            "family_id": source_id,
                            "title": "Temp rule (FICTIONAL STUB)",
                            "publisher": "BenefitIQ Stub Corpus",
                            "url": "https://example.test/stub/tmp-rule",
                            "effective_from": "2024-01-01",
                            "doc_type": "coding_rules",
                            "loader": "structured",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    write_rows({"description": "should not be stored"})
    with pytest.raises(IngestionError):
        ingest_manifest(manifest)
    with session_scope() as session:
        assert session.get(Source, source_id) is None

    write_rows()
    try:
        inserted = ingest_manifest(manifest)
        assert inserted["inserted"] == 1
        again = ingest_manifest(manifest)
        assert again["skipped"] == 1

        document.write_text(document.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        with pytest.raises(IngestionError, match="overwrite"):
            ingest_manifest(manifest)
        with session_scope() as session:
            stored = session.get(Source, source_id)
            assert stored is not None
            assert stored.url == "https://example.test/stub/tmp-rule"
            assert session.scalar(select(Source.source_id).where(Source.source_id == source_id)) == source_id
    finally:
        _delete_source(source_id)
