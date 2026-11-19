"""Health check, headers, chat flag, and the deploy command's env contract."""

import os
import subprocess

from fastapi.testclient import TestClient

from benefitiq.config import chat_enabled, pilot_missing
from benefitiq.constants import PILOT_BANNER
from benefitiq.db import reset_engine
from benefitiq.main import app
from tests.conftest import ROOT

client = TestClient(app)


def test_healthz_is_ok_and_headers_are_set():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "sources" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers


def test_healthz_fails_closed_without_database_url(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_ENV", "pilot")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine()
    try:
        response = client.get("/healthz")
    finally:
        reset_engine()
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "benefitiq:" not in response.text
    assert pilot_missing() == [] or "DATABASE_URL" in pilot_missing()


def test_pilot_hsts_and_missing_names(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_ENV", "pilot")
    monkeypatch.setenv("BENEFITIQ_ACCESS_ENFORCE", "1")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCESS_AUD", raising=False)
    names = pilot_missing()
    assert "DATABASE_URL" not in names
    assert "ADMIN_EMAILS" in names
    assert "CLOUDFLARE_ACCESS_AUD" in names
    page = client.get("/")
    assert page.headers["strict-transport-security"].startswith("max-age=")
    assert PILOT_BANNER in page.text


def test_chat_stays_disabled_with_or_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert chat_enabled() is False
    page = client.get("/cases/SYN-DEMO-0001")
    assert 'data-chat-enabled="no"' in page.text
    textarea = page.text.split('id="case-chat-input"', 1)[1]
    assert "disabled" in page.text
    assert "openai" not in page.text.lower()
    missing = client.post("/api/chat", json={"message": "synthetic question"})
    assert missing.status_code == 404

    monkeypatch.setenv("OPENAI_API_KEY", "present-for-test")
    assert chat_enabled() is True
    enabled = client.get("/cases/SYN-DEMO-0001")
    assert 'data-chat-enabled="yes"' in enabled.text
    assert "disabled" in enabled.text
    assert "present-for-test" not in enabled.text
    assert client.post("/api/chat", json={"message": "synthetic question"}).status_code == 404
    assert "disabled" in textarea


def test_deploy_script_does_not_print_secret_values():
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATABASE_URL": "postgresql://user:secret-value@db.example/app",
    }
    result = subprocess.run(
        ["./scripts/deploy_pilot.sh"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "secret-value" not in combined
    assert "missing CLOUDFLARE_API_TOKEN" in combined
    config = (ROOT / "wrangler.jsonc").read_text(encoding="utf-8")
    assert "postgres" not in config
    assert "secret-value" not in config
    worker = (ROOT / "worker" / "index.js").read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN" not in worker
