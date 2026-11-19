"""Who may open the pilot.

Cloudflare Access proves the email with a signed token. This app then allows
that email only when it is an admin or an approved request. Revoking a request
blocks the next page load. The app does not change the Access policy.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import httpx
import jwt
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from benefitiq.config import access_enforced
from benefitiq.db import session_scope
from benefitiq.engine.recommend import AUDIT, configure_audit_log
from benefitiq.models import AccessRequest
from benefitiq.office.session import signing_key
from benefitiq.phi_screen import phi_reason, valid_work_email

logger = logging.getLogger("benefitiq.access")

_RATE: dict[str, list[float]] = {}
_RATE_LIMIT = 5
_RATE_WINDOW_SECONDS = 600
_jwks: PyJWKClient | None = None
_jwks_url: str | None = None


class AccessDenied(Exception):
    def __init__(self, status_code: int, heading: str, message: str) -> None:
        self.status_code = status_code
        self.heading = heading
        self.message = message


def admin_emails() -> set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def team_domain() -> str:
    raw = os.environ.get("CLOUDFLARE_ACCESS_TEAM_DOMAIN", "").strip()
    raw = raw.removeprefix("https://").removeprefix("http://").strip("/")
    return raw


def access_audience() -> str:
    return os.environ.get("CLOUDFLARE_ACCESS_AUD", "").strip()


def turnstile_site_key() -> str:
    return os.environ.get("TURNSTILE_SITE_KEY", "").strip()


def turnstile_secret() -> str:
    return os.environ.get("TURNSTILE_SECRET_KEY", "").strip()


def turnstile_active() -> bool:
    return bool(turnstile_site_key() and turnstile_secret())


def reset_rate_limit() -> None:
    _RATE.clear()


def reset_jwks() -> None:
    global _jwks, _jwks_url
    _jwks = None
    _jwks_url = None


def allow_request(client_key: str) -> bool:
    now = time.monotonic()
    recent = [stamp for stamp in _RATE.get(client_key, []) if now - stamp < _RATE_WINDOW_SECONDS]
    if len(recent) >= _RATE_LIMIT:
        _RATE[client_key] = recent
        return False
    recent.append(now)
    _RATE[client_key] = recent
    return True


def client_key(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip", "").strip()
    if forwarded:
        return forwarded
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _signing_key(token: str):
    global _jwks, _jwks_url
    domain = team_domain()
    if not domain:
        raise AccessDenied(401, "Sign in required", "Sign in to open BenefitIQ.")
    url = f"https://{domain}/cdn-cgi/access/certs"
    if _jwks is None or _jwks_url != url:
        _jwks = PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)
        _jwks_url = url
    return _jwks.get_signing_key_from_jwt(token).key


def email_from_token(token: str, key) -> str:
    domain = team_domain()
    audience = access_audience()
    if not domain or not audience:
        raise jwt.InvalidTokenError("access is not configured")
    payload = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=audience,
        issuer=f"https://{domain}",
        leeway=30,
        options={"require": ["exp", "iat", "iss", "aud", "email"]},
    )
    email = payload.get("email")
    if not isinstance(email, str) or not valid_work_email(email):
        raise jwt.InvalidTokenError("missing email")
    return email.strip().lower()


def verify_access_token(token: str) -> str | None:
    if not token or token.count(".") != 2:
        return None
    try:
        return email_from_token(token, _signing_key(token))
    except Exception:
        return None


def token_from_request(request: Request) -> str:
    header = request.headers.get("cf-access-jwt-assertion", "").strip()
    if header:
        return header
    return request.cookies.get("CF_Authorization", "").strip()


def email_is_approved(email: str) -> bool:
    try:
        with session_scope() as session:
            row = session.scalar(select(AccessRequest).where(AccessRequest.email == email))
            return row is not None and row.status == "approved"
    except Exception:
        logger.info("access_lookup result=failed")
        return False


def is_admin_email(email: str | None) -> bool:
    allowed = admin_emails()
    if not allowed:
        return not access_enforced()
    return email is not None and email in allowed


def identify(request: Request) -> tuple[str | None, bool]:
    """Return the signed-in email and whether that email is an admin.

    When enforcement is off, local development stays open. A spoofed email
    header is ignored in both modes.
    """
    if not access_enforced():
        dev = os.environ.get("BENEFITIQ_DEV_EMAIL", "").strip().lower()
        email = dev or None
        return email, is_admin_email(email)
    email = verify_access_token(token_from_request(request))
    if not email:
        raise AccessDenied(
            401,
            "Sign in required",
            "Sign in to open BenefitIQ. Use the link your office shared.",
        )
    if is_admin_email(email) or email_is_approved(email):
        return email, is_admin_email(email)
    raise AccessDenied(
        403,
        "Access is not approved yet",
        "This pilot is only open to people who have been approved. "
        "Request access if you have not already, then wait for a yes.",
    )


def csrf_token(email: str | None) -> str:
    import hashlib
    import hmac

    identity = email or "local"
    return hmac.new(signing_key(), f"access:{identity}".encode(), hashlib.sha256).hexdigest()


def csrf_ok(email: str | None, token: str) -> bool:
    import hmac

    if not token:
        return False
    return hmac.compare_digest(csrf_token(email), token)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def format_when(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def clean_staff_field(value: str, *, limit: int) -> str | None:
    text = " ".join(value.split())
    if not text or len(text) > limit:
        return None
    if phi_reason(text, allow_email=False):
        return None
    return text


def submit_request(name: str, email: str, office_name: str, role: str) -> tuple[str, str]:
    """Store a staff request. Returns (outcome, message). Outcome is ok or rejected."""
    cleaned_name = clean_staff_field(name, limit=80)
    cleaned_office = clean_staff_field(office_name, limit=120)
    cleaned_role = clean_staff_field(role, limit=80)
    cleaned_email = email.strip().lower()
    if (
        cleaned_name is None
        or cleaned_office is None
        or cleaned_role is None
        or not valid_work_email(cleaned_email)
        or phi_reason(cleaned_email, allow_email=True)
    ):
        return "rejected", (
            "Check the form and try again. Use your own name and work email. "
            "Do not include patient details."
        )
    now = _now()
    notify = False
    with session_scope() as session:
        row = session.scalar(select(AccessRequest).where(AccessRequest.email == cleaned_email))
        if row is None:
            session.add(
                AccessRequest(
                    name=cleaned_name,
                    email=cleaned_email,
                    office_name=cleaned_office,
                    role=cleaned_role,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
            notify = True
            outcome = "received"
        elif row.status == "pending":
            outcome = "waiting"
        elif row.status == "approved":
            outcome = "approved"
        else:
            row.name = cleaned_name
            row.office_name = cleaned_office
            row.role = cleaned_role
            row.status = "pending"
            row.created_at = now
            row.updated_at = now
            row.decided_at = None
            row.decided_by = None
            notify = True
            outcome = "received"
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            outcome = "waiting"
            notify = False
    if notify:
        _notify(cleaned_email)
    messages = {
        "received": "Request received. The person who approves access will review it. You can close this page.",
        "waiting": "A request for that work email is already waiting.",
        "approved": "That work email already has access. Open the office link and sign in.",
    }
    return "ok", messages[outcome]


def _notify(email: str) -> None:
    url = os.environ.get("ACCESS_NOTIFY_WEBHOOK_URL", "").strip()
    if not url:
        return
    configure_audit_log()
    try:
        with session_scope() as session:
            row = session.scalar(select(AccessRequest).where(AccessRequest.email == email))
            if row is None:
                return
            payload = {
                "event": "access_request",
                "email": row.email,
                "name": row.name,
                "office": row.office_name,
                "role": row.role,
            }
        httpx.post(url, json=payload, timeout=5.0, follow_redirects=False)
        AUDIT.info("access_notify result=ok")
    except Exception:
        AUDIT.info("access_notify result=failed")


def list_requests() -> dict[str, list[AccessRequest]]:
    grouped: dict[str, list[AccessRequest]] = {
        "pending": [],
        "approved": [],
        "denied": [],
        "revoked": [],
    }
    with session_scope() as session:
        rows = session.scalars(select(AccessRequest).order_by(AccessRequest.created_at.desc())).all()
        for row in rows:
            session.expunge(row)
            grouped.setdefault(row.status, []).append(row)
    return grouped


def decide_request(request_id: int, action: str, actor: str) -> str:
    allowed = {
        "approve": {"pending", "denied", "revoked"},
        "deny": {"pending"},
        "revoke": {"approved"},
    }
    if action not in allowed:
        return "no"
    now = _now()
    with session_scope() as session:
        row = session.get(AccessRequest, request_id)
        if row is None or row.status not in allowed[action]:
            return "no"
        row.status = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[action]
        row.decided_at = now
        row.decided_by = actor
        row.updated_at = now
        target = row.email
    configure_audit_log()
    AUDIT.info(
        "access_decision action=%s actor=%s email=%s at=%s",
        action,
        actor,
        target,
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return "yes"


def verify_turnstile(token: str, remote_ip: str) -> bool:
    if not turnstile_active():
        return True
    if not token:
        return False
    try:
        response = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": turnstile_secret(), "response": token, "remoteip": remote_ip},
            timeout=5.0,
        )
        payload = response.json()
    except Exception:
        return False
    return bool(isinstance(payload, dict) and payload.get("success") is True)
