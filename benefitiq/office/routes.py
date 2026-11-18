"""Four-step office flow. The engineer API stays on its own routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from benefitiq.constants import DISCLAIMER
from benefitiq.office.decide import citations_for, decide
from benefitiq.office.session import (
    COOKIE,
    decode_state,
    encode_state,
    new_case_id,
    office_today,
)
from benefitiq.office.tree import load_tree, procedure_by_id

router = APIRouter()


def _templates():
    from benefitiq.main import templates

    return templates


def _render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    context.setdefault("disclaimer", DISCLAIMER)
    response = _templates().TemplateResponse(request, name, context, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _redirect(url: str, state: dict | None = None, *, clear: bool = False) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    if clear:
        response.delete_cookie(COOKIE, path="/")
    elif state is not None:
        response.set_cookie(COOKIE, encode_state(state), httponly=True, samesite="lax", path="/")
    return response


def _state(request: Request) -> dict | None:
    return decode_state(request.cookies.get(COOKIE))


def _error(request: Request, message: str, status_code: int) -> HTMLResponse:
    return _render(
        request,
        "error.html",
        {
            "message": message,
            "heading": "Please try again",
            "back_href": "/",
            "back_label": "Back to the start",
        },
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render(
        request,
        "index.html",
        {"procedures": load_tree()["procedures"], "step": 1},
    )


@router.post("/office/start")
def start(request: Request, procedure_id: str = Form()):
    procedure = procedure_by_id(procedure_id)
    if procedure is None:
        return _error(request, "That procedure is not on the list.", 404)
    if not procedure["active"]:
        return _error(request, "That procedure is not available yet.", 400)
    state = {
        "case_id": new_case_id(),
        "procedure_id": procedure["id"],
        "as_of": office_today().isoformat(),
        "index": 0,
        "answers": {},
    }
    return _redirect("/office/question", state)


@router.get("/office/question", response_class=HTMLResponse)
def question(request: Request):
    state = _state(request)
    if state is None:
        return _redirect("/")
    procedure = procedure_by_id(state["procedure_id"])
    if procedure is None or not procedure["active"]:
        return _redirect("/", clear=True)
    questions = procedure["questions"]
    index = state["index"]
    if index >= len(questions):
        return _redirect("/office/result", state)
    current = questions[index]
    try:
        as_of = date.fromisoformat(state["as_of"])
    except ValueError:
        return _redirect("/", clear=True)
    previous = state["answers"].get(current["id"])
    return _render(
        request,
        "question.html",
        {
            "step": 2,
            "procedure": procedure,
            "question": current,
            "number": index + 1,
            "total": len(questions),
            "previous": {"yes": "Yes", "no": "No", "not_sure": "Not sure"}.get(previous or ""),
            "citations": citations_for(current["citations"], as_of),
        },
    )


@router.post("/office/answer")
def answer(request: Request, choice: str = Form()):
    state = _state(request)
    if state is None:
        return _redirect("/")
    if choice not in {"yes", "no", "not_sure"}:
        return _error(request, "Choose yes, no, or not sure.", 400)
    procedure = procedure_by_id(state["procedure_id"])
    if procedure is None or not procedure["active"]:
        return _redirect("/", clear=True)
    questions = procedure["questions"]
    index = state["index"]
    if index >= len(questions):
        return _redirect("/office/result", state)
    state["answers"][questions[index]["id"]] = choice
    state["index"] = index + 1
    destination = "/office/result" if state["index"] >= len(questions) else "/office/question"
    return _redirect(destination, state)


@router.post("/office/back")
def back(request: Request):
    state = _state(request)
    if state is None:
        return _redirect("/")
    if state["index"] <= 0:
        return _redirect("/", clear=True)
    state["index"] -= 1
    return _redirect("/office/question", state)


def _finished(request: Request):
    state = _state(request)
    if state is None:
        return None, _redirect("/")
    procedure = procedure_by_id(state["procedure_id"])
    if procedure is None or not procedure["active"]:
        return None, _redirect("/", clear=True)
    if state["index"] < len(procedure["questions"]):
        return None, _redirect("/office/question", state)
    try:
        as_of = date.fromisoformat(state["as_of"])
    except ValueError:
        return None, _redirect("/", clear=True)
    decision = decide(
        procedure["id"],
        state["answers"],
        as_of=as_of,
        case_id=state["case_id"],
    )
    return decision, None


@router.get("/office/result", response_class=HTMLResponse)
def result(request: Request):
    decision, early = _finished(request)
    if early is not None:
        return early
    return _render(request, "decision.html", {"step": 3, "decision": decision})


@router.get("/office/print", response_class=HTMLResponse)
def print_summary(request: Request):
    decision, early = _finished(request)
    if early is not None:
        return early
    return _render(request, "summary.html", {"step": 4, "decision": decision})
