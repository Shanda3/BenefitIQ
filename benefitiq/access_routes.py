"""Public request form and the admin approval page."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from benefitiq.access_control import (
    allow_request,
    client_key,
    csrf_ok,
    csrf_token,
    decide_request,
    format_when,
    list_requests,
    submit_request,
    turnstile_active,
    turnstile_site_key,
    verify_turnstile,
)
from benefitiq.constants import DISCLAIMER, PILOT_BANNER

router = APIRouter()


def _templates():
    from benefitiq.main import templates

    return templates


def _page(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    context.setdefault("disclaimer", DISCLAIMER)
    context.setdefault("pilot_banner", PILOT_BANNER)
    response = _templates().TemplateResponse(request, name, context, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _actor(request: Request) -> str:
    email = getattr(request.state, "email", None)
    if isinstance(email, str) and email:
        return email
    return "local"


@router.get("/request-access", response_class=HTMLResponse)
def request_access_form(request: Request):
    return _page(
        request,
        "request_access.html",
        {
            "nav": "",
            "turnstile": turnstile_active(),
            "turnstile_site_key": turnstile_site_key(),
            "notice": "",
        },
    )


@router.post("/request-access", response_class=HTMLResponse)
async def request_access_submit(request: Request):
    form = await request.form()
    name = str(form.get("name") or "")
    email = str(form.get("email") or "")
    office_name = str(form.get("office_name") or "")
    role = str(form.get("role") or "")
    cf_turnstile_response = str(form.get("cf-turnstile-response") or "")
    if not allow_request(client_key(request)):
        return _page(
            request,
            "request_access.html",
            {
                "nav": "",
                "turnstile": turnstile_active(),
                "turnstile_site_key": turnstile_site_key(),
                "notice": "Too many requests from this network. Wait a few minutes and try again.",
            },
            status_code=429,
        )
    if turnstile_active() and not verify_turnstile(cf_turnstile_response, client_key(request)):
        return _page(
            request,
            "request_access.html",
            {
                "nav": "",
                "turnstile": True,
                "turnstile_site_key": turnstile_site_key(),
                "notice": "The extra check did not succeed. Try again.",
            },
            status_code=400,
        )
    outcome, message = submit_request(name, email, office_name, role)
    if outcome != "ok":
        return _page(
            request,
            "request_access.html",
            {
                "nav": "",
                "turnstile": turnstile_active(),
                "turnstile_site_key": turnstile_site_key(),
                "notice": message,
            },
            status_code=422,
        )
    return _page(
        request,
        "request_received.html",
        {"nav": "", "message": message},
    )


@router.get("/access", response_class=HTMLResponse)
def access_admin(request: Request):
    grouped = list_requests()
    pending = grouped["pending"]
    count = len(pending)
    if count == 0:
        waiting = "Nobody is waiting."
    elif count == 1:
        waiting = "1 person is waiting."
    else:
        waiting = f"{count} people are waiting."
    return _page(
        request,
        "access_admin.html",
        {
            "nav": "access",
            "waiting": waiting,
            "pending_count": count,
            "groups": grouped,
            "csrf": csrf_token(getattr(request.state, "email", None)),
            "format_when": format_when,
        },
    )


def _decide(request: Request, action: str, request_id: int, token: str) -> RedirectResponse:
    email = getattr(request.state, "email", None)
    if not csrf_ok(email if isinstance(email, str) else None, token):
        return RedirectResponse("/access", status_code=303)
    actor = _actor(request)
    decide_request(request_id, action, actor)
    response = RedirectResponse("/access", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/access/approve")
def approve(request: Request, request_id: int = Form(), token: str = Form("")):
    return _decide(request, "approve", request_id, token)


@router.post("/access/deny")
def deny(request: Request, request_id: int = Form(), token: str = Form("")):
    return _decide(request, "deny", request_id, token)


@router.post("/access/revoke")
def revoke(request: Request, request_id: int = Form(), token: str = Form("")):
    return _decide(request, "revoke", request_id, token)
