import json
from datetime import date
from pathlib import Path

import pytest

from benefitiq.db import init_db
from benefitiq.engine.recommend import evaluate
from benefitiq.ingestion.service import ingest_manifest
from benefitiq.schemas import CaseInput

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus" / "stub" / "manifest.yaml"
OFFICE_MANIFEST = ROOT / "corpus" / "office" / "manifest.yaml"
PAYER_MANIFEST = ROOT / "corpus" / "payers" / "manifest.yaml"
SAMPLES = ROOT / "samples"


@pytest.fixture(scope="session", autouse=True)
def stub_corpus():
    init_db(drop=True)
    ingest_manifest(MANIFEST)
    ingest_manifest(OFFICE_MANIFEST)
    ingest_manifest(PAYER_MANIFEST)
    yield


def load_case(name: str, **overrides) -> CaseInput:
    payload = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
    if "date_of_service" in overrides and isinstance(overrides["date_of_service"], date):
        overrides["date_of_service"] = overrides["date_of_service"].isoformat()
    payload.update(overrides)
    return CaseInput.model_validate(payload)


def evaluate_sample(name: str, **overrides):
    return evaluate(load_case(name, **overrides))
