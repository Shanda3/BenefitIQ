"""Shape of a later dental-plan check. Nothing here is an active procedure."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "corpus" / "payers" / "dental_plan.yaml"
FEATURE_IDS = (
    "frequency_limit",
    "waiting_period",
    "alternate_benefit",
    "prior_authorization",
)


@lru_cache(maxsize=1)
def dental_plan() -> dict:
    loaded = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("dental plan slot must be a mapping")
    if loaded.get("benefit_path") != "dental":
        raise ValueError("dental plan slot must use benefit_path dental")
    if loaded.get("code_system") != "CDT":
        raise ValueError("dental plan slot must use CDT code numbers")
    features = loaded.get("features") or []
    found = tuple(item.get("id") for item in features)
    if found != FEATURE_IDS:
        raise ValueError("dental plan features do not match the expected set")
    payers = loaded.get("payers") or []
    if not payers or any(item.get("status") != "not_loaded" for item in payers):
        raise ValueError("dental plan payers stay unloaded until a booklet is added")
    return loaded
