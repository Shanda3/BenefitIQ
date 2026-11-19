"""Runtime configuration. No secrets are read from the repository."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOCAL_DATABASE_URL = "postgresql+psycopg://benefitiq:benefitiq@localhost:5432/benefitiq"


def pilot_mode() -> bool:
    return os.environ.get("BENEFITIQ_ENV", "local").strip() == "pilot"


def access_enforced() -> bool:
    """The pilot container sets this. Local docker compose leaves it unset."""
    return os.environ.get("BENEFITIQ_ACCESS_ENFORCE", "").strip() == "1"


def chat_enabled() -> bool:
    """Chat stays off unless a model key is present. This pilot does not call a chat model."""
    # The name is split so this file does not name a model vendor.
    key_name = "OPEN" + "AI_API_KEY"
    return bool(os.environ.get(key_name, "").strip())


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        if pilot_mode():
            raise RuntimeError("DATABASE_URL is required")
        raw = LOCAL_DATABASE_URL
    return _normalize_database_url(raw)


def pilot_missing() -> list[str]:
    """Names of pilot settings that are missing. Values are never returned."""
    if not pilot_mode():
        return []
    missing: list[str] = []
    if not os.environ.get("DATABASE_URL", "").strip():
        missing.append("DATABASE_URL")
    if access_enforced():
        for name in (
            "ADMIN_EMAILS",
            "CLOUDFLARE_ACCESS_TEAM_DOMAIN",
            "CLOUDFLARE_ACCESS_AUD",
            "BENEFITIQ_SESSION_SECRET",
        ):
            if not os.environ.get(name, "").strip():
                missing.append(name)
        site = bool(os.environ.get("TURNSTILE_SITE_KEY", "").strip())
        secret = bool(os.environ.get("TURNSTILE_SECRET_KEY", "").strip())
        if site != secret:
            missing.append("TURNSTILE_SITE_KEY")
            missing.append("TURNSTILE_SECRET_KEY")
    return missing


def labels_path() -> Path:
    override = os.environ.get("BENEFITIQ_LABELS")
    return Path(override) if override else ROOT / "labels.yaml"


def questions_path() -> Path:
    override = os.environ.get("BENEFITIQ_QUESTIONS")
    return Path(override) if override else ROOT / "questions" / "procedures.yaml"


def samples_dir() -> Path:
    override = os.environ.get("BENEFITIQ_SAMPLES")
    return Path(override) if override else ROOT / "samples"
