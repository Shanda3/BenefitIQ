"""Signed browser-session state. The server does not keep a copy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import date
from typing import Any

COOKIE = "benefitiq_office"
_SECRET: bytes | None = None


def office_today() -> date:
    override = os.environ.get("BENEFITIQ_TODAY")
    if override:
        return date.fromisoformat(override)
    return date.today()


def new_case_id() -> str:
    return "SYN-OFFICE-" + secrets.token_hex(4).upper()


def signing_key() -> bytes:
    return _secret()


def _secret() -> bytes:
    global _SECRET
    env = os.environ.get("BENEFITIQ_SESSION_SECRET")
    if env:
        return env.encode("utf-8")
    if _SECRET is None:
        _SECRET = secrets.token_hex(32).encode("utf-8")
    return _SECRET


def encode_state(state: dict[str, Any]) -> str:
    raw = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_state(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    case_id = payload.get("case_id")
    procedure_id = payload.get("procedure_id")
    as_of = payload.get("as_of")
    index = payload.get("index")
    answers = payload.get("answers")
    if not isinstance(case_id, str) or not case_id.startswith("SYN-"):
        return None
    if not isinstance(procedure_id, str) or not procedure_id:
        return None
    if not isinstance(as_of, str):
        return None
    if not isinstance(index, int) or index < 0:
        return None
    if not isinstance(answers, dict):
        return None
    clean_answers: dict[str, str] = {}
    for key, value in answers.items():
        if isinstance(key, str) and value in {"yes", "no", "not_sure"}:
            clean_answers[key] = value
    payer_id = payload.get("payer_id")
    segment_id = payload.get("segment_id")
    if payer_id is not None and not isinstance(payer_id, str):
        return None
    if segment_id is not None and not isinstance(segment_id, str):
        return None
    return {
        "case_id": case_id,
        "procedure_id": procedure_id,
        "as_of": as_of,
        "index": index,
        "answers": clean_answers,
        "payer_id": payer_id or None,
        "segment_id": segment_id or None,
    }
