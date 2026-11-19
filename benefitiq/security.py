"""Security headers, request log, and the pilot fail-closed check.

Request logs record the path and the status. They do not record the query string
or the body. A log line that still looks like patient information is replaced
with a reason code.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote_plus

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from benefitiq.config import access_enforced, pilot_missing
from benefitiq.constants import DISCLAIMER, PHI_REJECTION, PILOT_BANNER
from benefitiq.engine.recommend import AUDIT, configure_audit_log
from benefitiq.phi_screen import phi_reason

_FILTER_INSTALLED = False
_MAX_BODY = 100_000

PUBLIC_PATHS = {"/healthz", "/request-access"}


def install_phi_log_filter() -> None:
    """Replace patient-shaped text in every log line. Staff emails in audit lines stay."""
    global _FILTER_INSTALLED
    if _FILTER_INSTALLED:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:
            return record
        reason = phi_reason(message, allow_email=True)
        if reason:
            record.msg = "redacted reason=%s"
            record.args = (reason,)
        return record

    logging.setLogRecordFactory(factory)
    configure_audit_log()
    _FILTER_INSTALLED = True


def require_pilot_config() -> None:
    missing = pilot_missing()
    if missing:
        raise RuntimeError("pilot configuration missing: " + ", ".join(missing))


def security_headers(response, *, pilot: bool) -> None:
    from benefitiq.access_control import turnstile_active

    script = "script-src 'self'"
    frame = "frame-src 'none'"
    if turnstile_active():
        script += " https://challenges.cloudflare.com"
        frame = "frame-src https://challenges.cloudflare.com"
    policy = "; ".join(
        [
            "default-src 'self'",
            "img-src 'self' data:",
            "style-src 'self'",
            script,
            frame,
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )
    response.headers["Content-Security-Policy"] = policy
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if pilot:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if not response.headers.get("Cache-Control"):
        response.headers["Cache-Control"] = "no-store"


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/static/")


def _html_error(
    request: Request,
    heading: str,
    message: str,
    status_code: int,
    *,
    message_testid: str = "",
    back_href: str = "/request-access",
    back_label: str = "Request access",
) -> HTMLResponse:
    from benefitiq.main import templates

    response = templates.TemplateResponse(
        request,
        "error.html",
        {
            "disclaimer": DISCLAIMER,
            "pilot_banner": PILOT_BANNER,
            "heading": heading,
            "message": message,
            "message_testid": message_testid,
            "back_href": back_href,
            "back_label": back_label,
            "nav": "",
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _rejected(request: Request, reason: str):
    AUDIT.info("phi_rejected route=%s reason=%s", request.url.path, reason)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=422,
            content={"disclaimer": DISCLAIMER, "message": PHI_REJECTION},
        )
    return _html_error(
        request,
        "Please remove patient details",
        PHI_REJECTION,
        422,
        message_testid="phi-rejection",
        back_href="/",
        back_label="Back to the start",
    )


async def _screen_input(request: Request):
    if request.url.path.startswith("/static/"):
        return None
    query = unquote_plus(request.url.query)
    reason = phi_reason(query, allow_email=False)
    if reason:
        return _rejected(request, reason)
    if request.method not in {"POST", "PUT", "PATCH"}:
        return None
    body = await request.body()
    if len(body) > _MAX_BODY:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=413,
                content={"disclaimer": DISCLAIMER, "message": "That entry is too long."},
            )
        return _html_error(
            request,
            "Please try again",
            "That entry is too long.",
            413,
            back_href="/",
            back_label="Back to the start",
        )
    text = body.decode("utf-8", errors="replace")
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        text = unquote_plus(text)
    allow_email = request.url.path == "/request-access"
    reason = phi_reason(text, allow_email=allow_email)
    if reason:
        return _rejected(request, reason)
    return None


def _blocked(request: Request, status_code: int, heading: str, message: str):
    from benefitiq.config import pilot_mode

    if request.url.path.startswith("/api/"):
        response = JSONResponse(
            status_code=status_code,
            content={"disclaimer": DISCLAIMER, "message": message},
        )
    else:
        response = _html_error(request, heading, message, status_code)
    security_headers(response, pilot=pilot_mode())
    AUDIT.info("request method=%s path=%s status=%s", request.method, request.url.path, status_code)
    return response


def _apply_identity(request: Request) -> tuple[str | None, bool]:
    from benefitiq.access_control import identify, is_admin_email, token_from_request, verify_access_token

    if not access_enforced():
        return identify(request)
    email = verify_access_token(token_from_request(request))
    return email, is_admin_email(email)


async def pilot_middleware(request: Request, call_next):
    from benefitiq.access_control import admin_emails, email_is_approved
    from benefitiq.config import pilot_mode

    request.state.email = None
    request.state.is_admin = False
    screened = await _screen_input(request)
    if screened is not None:
        security_headers(screened, pilot=pilot_mode())
        return screened

    email, admin = _apply_identity(request)
    request.state.email = email
    request.state.is_admin = admin
    path = request.url.path
    admin_area = path == "/access" or path.startswith("/access/")
    if access_enforced() and not _is_public(path):
        if not email:
            return _blocked(
                request,
                401,
                "Sign in required",
                "Sign in to open BenefitIQ. Use the link your office shared.",
            )
        if admin_area and not admin:
            return _blocked(
                request,
                403,
                "That page is not available",
                "Only the person who approves access can open that page.",
            )
        if not admin and not email_is_approved(email):
            return _blocked(
                request,
                403,
                "Access is not approved yet",
                "This pilot is only open to people who have been approved. "
                "Request access if you have not already, then wait for a yes.",
            )
    elif admin_area and admin_emails() and not admin:
        return _blocked(
            request,
            403,
            "That page is not available",
            "Only the person who approves access can open that page.",
        )

    response = await call_next(request)
    if not path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    security_headers(response, pilot=pilot_mode())
    if not path.startswith("/static/"):
        AUDIT.info("request method=%s path=%s status=%s", request.method, path, response.status_code)
    return response
