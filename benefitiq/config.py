"""Runtime configuration. No secrets are read from the repository."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://benefitiq:benefitiq@localhost:5432/benefitiq",
    )


def labels_path() -> Path:
    override = os.environ.get("BENEFITIQ_LABELS")
    return Path(override) if override else ROOT / "labels.yaml"


def samples_dir() -> Path:
    override = os.environ.get("BENEFITIQ_SAMPLES")
    return Path(override) if override else ROOT / "samples"
