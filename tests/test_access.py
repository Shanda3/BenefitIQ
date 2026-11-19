"""Request access, and the approve, deny, and revoke gate."""

import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text

from benefitiq.access_control import reset_jwks, reset_rate_limit
from benefitiq.db import session_scope
from benefitiq.main import app

TEAM = "team.cloudflareaccess.com"
AUD = "aud-test"
ADMIN = "admin@example.com"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUBLIC = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_rate_limit()
    reset_jwks()
    monkeypatch.setattr("benefitiq.access_control._signing_key", lambda _token: _PUBLIC)
    with session_scope() as session:
        session.execute(text("DELETE FROM access_requests"))
    yield
    with session_scope() as session:
        session.execute(text("DELETE FROM access_requests"))
    reset_rate_limit()


def _enforce(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_ACCESS_ENFORCE", "1")
    monkeypatch.setenv("CLOUDFLARE_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CLOUDFLARE_ACCESS_AUD", AUD)
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.setenv("BENEFITIQ_SESSION_SECRET", "test-session-secret")


def _token(email: str, *, audience: str = AUD, expires_in: int = 600) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "email": email,
            "aud": audience,
            "iss": f"https://{TEAM}",
            "iat": now,
            "nbf": now,
            "exp": now + expires_in,
            "sub": email,
            "type": "app",
        },
        _PRIVATE,
        algorithm="RS256",
    )


def _headers(email: str, **kwargs) -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": _token(email, **kwargs)}


def _client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def _submit(client: TestClient, email: str, *, name: str = "Ada Office", office: str = "Example Dental"):
    return client.post(
        "/request-access",
        data={
            "name": name,
            "email": email,
            "office_name": office,
            "role": "Biller",
        },
    )


def _csrf(page_text: str) -> str:
    marker = 'name="token" value="'
    start = page_text.index(marker) + len(marker)
    return page_text[start : page_text.index('"', start)]


def test_request_page_is_public_and_stores_only_staff_fields(monkeypatch):
    _enforce(monkeypatch)
    client = _client()
    page = client.get("/request-access")
    assert page.status_code == 200
    assert 'data-testid="request-access-form"' in page.text
    assert "patient" in page.text.lower()
    posted = _submit(client, "biller@example.com", office="OFFICE-SENTINEL")
    assert posted.status_code == 200
    assert "Request received" in posted.text
    with session_scope() as session:
        row = session.execute(
            text("SELECT name, email, office_name, role, status FROM access_requests")
        ).one()
    assert row.email == "biller@example.com"
    assert row.status == "pending"
    assert row.office_name == "OFFICE-SENTINEL"


def test_duplicate_request_stays_pending(monkeypatch):
    _enforce(monkeypatch)
    client = _client()
    assert _submit(client, "biller@example.com").status_code == 200
    again = _submit(client, "biller@example.com")
    assert again.status_code == 200
    assert "already waiting" in again.text
    with session_scope() as session:
        count = session.execute(text("SELECT count(*) FROM access_requests")).scalar()
    assert count == 1


def test_patient_shaped_request_is_not_stored():
    client = _client()
    secret = "123" + "-45-" + "6789"
    response = client.post(
        "/request-access",
        data={
            "name": "Ada " + secret,
            "email": "biller@example.com",
            "office_name": "Example Dental",
            "role": "Biller",
        },
    )
    assert response.status_code == 422
    assert secret not in response.text
    with session_scope() as session:
        count = session.execute(text("SELECT count(*) FROM access_requests")).scalar()
    assert count == 0


def test_rate_limit_blocks_the_sixth_request():
    client = _client()
    for index in range(5):
        response = _submit(client, f"person{index}@example.com")
        assert response.status_code == 200
    blocked = _submit(client, "person9@example.com")
    assert blocked.status_code == 429
    assert "Too many requests" in blocked.text


def test_turnstile_is_required_only_when_configured(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "site-test")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-test")
    client = _client()
    missing = _submit(client, "biller@example.com")
    assert missing.status_code == 400
    assert "extra check" in missing.text
    monkeypatch.setattr(
        "benefitiq.access_routes.verify_turnstile",
        lambda token, _ip: token == "ok-token",
    )
    accepted = client.post(
        "/request-access",
        data={
            "name": "Ada Office",
            "email": "biller@example.com",
            "office_name": "Example Dental",
            "role": "Biller",
            "cf-turnstile-response": "ok-token",
        },
    )
    assert accepted.status_code == 200
    assert "Request received" in accepted.text


def test_notify_is_optional_and_does_not_log_the_url(monkeypatch, caplog):
    secret_url = "https://hooks.example/notify/secret-path"
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        class Response:
            status_code = 200
        return Response()

    monkeypatch.setattr("benefitiq.access_control.httpx.post", fake_post)
    client = _client()
    with caplog.at_level(logging.INFO):
        _submit(client, "quiet@example.com")
    assert calls == []
    monkeypatch.setenv("ACCESS_NOTIFY_WEBHOOK_URL", secret_url)
    with caplog.at_level(logging.INFO):
        _submit(client, "loud@example.com")
    assert calls == [secret_url]
    assert secret_url not in caplog.text
    assert "access_notify result=ok" in caplog.text


def test_admin_page_and_approve_deny_revoke(monkeypatch, caplog):
    _enforce(monkeypatch)
    client = _client()
    open_page = client.get("/")
    assert open_page.status_code == 401
    spoofed = client.get("/", headers={"Cf-Access-Authenticated-User-Email": ADMIN})
    assert spoofed.status_code == 401

    _submit(client, "biller@example.com", office="OFFICE-SENTINEL", name="NAME-SENTINEL")
    _submit(client, "other@example.com", name="Other Person")
    waiting = client.get("/", headers=_headers("biller@example.com"))
    assert waiting.status_code == 403

    admin = client.get("/access", headers=_headers(ADMIN))
    assert admin.status_code == 200
    assert 'data-testid="access-admin"' in admin.text
    assert "2 people are waiting." in admin.text
    assert "biller@example.com" in admin.text
    assert "&lt;" not in admin.text or "NAME-SENTINEL" in admin.text
    token = _csrf(admin.text)
    biller_id = _id_for("biller@example.com")
    other_id = _id_for("other@example.com")

    stranger = client.get("/access", headers=_headers("biller@example.com"))
    assert stranger.status_code == 403
    assert "approves access" in stranger.text

    with caplog.at_level(logging.INFO, logger="benefitiq.audit"):
        denied = client.post(
            "/access/deny",
            headers=_headers(ADMIN),
            data={"request_id": other_id, "token": token},
        )
        approved = client.post(
            "/access/approve",
            headers=_headers(ADMIN),
            data={"request_id": biller_id, "token": token},
        )
    assert denied.status_code == 303
    assert approved.status_code == 303
    assert "OFFICE-SENTINEL" not in caplog.text
    assert "NAME-SENTINEL" not in caplog.text
    assert "access_decision action=deny" in caplog.text
    assert "access_decision action=approve" in caplog.text
    assert f"actor={ADMIN}" in caplog.text
    assert "email=biller@example.com" in caplog.text

    allowed = client.get("/cases", headers=_headers("biller@example.com"))
    assert allowed.status_code == 200
    still_out = client.get("/", headers=_headers("other@example.com"))
    assert still_out.status_code == 403

    revoked = client.post(
        "/access/revoke",
        headers=_headers(ADMIN),
        data={"request_id": biller_id, "token": token},
    )
    assert revoked.status_code == 303
    blocked = client.get("/cases", headers=_headers("biller@example.com"))
    assert blocked.status_code == 403
    demo = client.get("/cases/SYN-DEMO-0001", headers=_headers(ADMIN))
    assert demo.status_code == 200


def test_expired_and_wrong_audience_tokens_are_rejected(monkeypatch):
    _enforce(monkeypatch)
    client = _client()
    expired = client.get("/", headers={"Cf-Access-Jwt-Assertion": _token(ADMIN, expires_in=-120)})
    assert expired.status_code == 401
    wrong = client.get("/", headers={"Cf-Access-Jwt-Assertion": _token(ADMIN, audience="other-aud")})
    assert wrong.status_code == 401


def test_cookie_token_is_accepted(monkeypatch):
    _enforce(monkeypatch)
    client = _client()
    client.cookies.set("CF_Authorization", _token(ADMIN))
    page = client.get("/access")
    assert page.status_code == 200


def _id_for(email: str) -> int:
    with session_scope() as session:
        return session.execute(
            text("SELECT id FROM access_requests WHERE email = :email"),
            {"email": email},
        ).scalar_one()
