"""Placeholder labels for code numbers.

The file labels.yaml is the only place a short display handle is defined.
Nothing in this module returns a CDT or CPT descriptor.
"""

from __future__ import annotations

from functools import lru_cache

import yaml

from benefitiq.config import labels_path


@lru_cache(maxsize=1)
def load_labels() -> dict[str, str]:
    path = labels_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    codes = raw.get("codes") or {}
    if not isinstance(codes, dict):
        raise ValueError("labels.yaml must contain a codes mapping")
    cleaned: dict[str, str] = {}
    for code, label in codes.items():
        text = str(label).strip()
        if not text.startswith("placeholder:"):
            raise ValueError(f"label for {code} must start with 'placeholder:'")
        cleaned[str(code)] = text
    return cleaned


def label_for(code: str | None) -> str | None:
    if not code:
        return None
    return load_labels().get(code, "placeholder: unlabeled")
