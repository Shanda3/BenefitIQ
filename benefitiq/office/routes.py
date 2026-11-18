"""Four-step office flow. The engineer API stays on its own routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from benefitiq.constants import DISCLAIMER
from benefitiq.db import session_scope
from benefitiq.models import Source
from benefitiq.office.decide import citations_for, decide
from benefitiq.office.demo import demo_card, demo_cards
from benefitiq.office.present import BOARD_COLUMNS, board_status_for, chip_class, plain_gap
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
            "nav": "",
        },
        status_code=status_code,
    )


def _review_number(request: Request, total: int) -> int:
    raw = request.query_params.get("review", "1")
    try:
        number = int(raw)
    except ValueError:
        number = 1
    if total < 1:
        return 1
    return min(max(number, 1), total)


def _snapshot(decision, status: str) -> dict:
    """Browser-only summary. No clinical note, and the server does not store it."""

    def _cited(citations):
        for item in citations:
            if item.status == "cited" and item.url:
                return item
        return None

    checklist = []
    for item in decision.checklist:
        cited = _cited(item.citations)
        checklist.append(
            {
                "text": plain_gap(item.text),
                "url": cited.url if cited else None,
                "title": cited.title if cited else None,
                "effective_from": cited.effective_from.isoformat() if cited and cited.effective_from else None,
                "effective_to": cited.effective_to.isoformat() if cited and cited.effective_to else None,
            }
        )
    return {
        "case_id": decision.case_id,
        "synthetic": True,
        "demo": False,
        "label": "SYNTHETIC",
        "procedure": decision.procedure_title,
        "date": decision.as_of.isoformat(),
        "result_label": decision.result_label,
        "board_status": status,
        "reason": plain_gap(decision.reason.text),
        "checklist_total": len(decision.checklist),
        "checklist_checked": 0,
        "answers": [{"prompt": row.prompt, "display": row.display} for row in decision.answers],
        "codes": [
            {"code": code.code, "label": code.label, "note": plain_gap(code.note)}
            for code in decision.codes
        ],
        "checklist": checklist,
    }


def _registered_sources() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(Source)).all()
        copied = [
            {
                "source_id": row.source_id,
                "title": row.title,
                "publisher": row.publisher,
                "url": row.url,
                "effective_from": row.effective_from,
                "effective_to": row.effective_to,
                "kind": "Office" if row.source_id.startswith("cms-") else "Stub",
            }
            for row in rows
        ]
    copied.sort(key=lambda item: (item["kind"] != "Office", item["title"]))
    return copied


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render(
        request,
        "index.html",
        {"procedures": load_tree()["procedures"], "step": 1, "nav": "check"},
    )


@router.get("/cases", response_class=HTMLResponse)
def cases(request: Request, view: str = "board"):
    if view not in {"board", "list"}:
        view = "board"
    cards = demo_cards()
    grouped = {column: [] for column in BOARD_COLUMNS}
    for card in cards:
        grouped[card["board_status"]].append(card)
    return _render(
        request,
        "cases.html",
        {
            "nav": "cases",
            "view": view,
            "columns": BOARD_COLUMNS,
            "grouped": grouped,
            "cards": cards,
        },
    )


@router.get("/cases/local", response_class=HTMLResponse)
def local_case(request: Request):
    return _render(request, "local_case.html", {"nav": "cases"})


@router.get("/procedures", response_class=HTMLResponse)
def procedures(request: Request):
    return _render(
        request,
        "procedures.html",
        {"nav": "procedures", "procedures": load_tree()["procedures"]},
    )


@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    return _render(
        request,
        "sources.html",
        {"nav": "sources", "sources": _registered_sources()},
    )


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return _render(request, "help.html", {"nav": "help"})


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
            "nav": "check",
            "case_id": state["case_id"],
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
    if state["index"] >= len(questions) or all(item["id"] in state["answers"] for item in questions):
        state["index"] = len(questions)
        destination = "/office/result"
    else:
        destination = "/office/question"
    return _redirect(destination, state)


@router.post("/office/edit")
def edit_answer(request: Request, question_index: int = Form()):
    state = _state(request)
    if state is None:
        return _redirect("/")
    procedure = procedure_by_id(state["procedure_id"])
    if procedure is None or not procedure["active"]:
        return _redirect("/", clear=True)
    questions = procedure["questions"]
    if question_index < 0 or question_index >= len(questions):
        return _error(request, "That question is not part of this check.", 400)
    if questions[question_index]["id"] not in state["answers"]:
        return _redirect("/office/question", state)
    state["index"] = question_index
    return _redirect("/office/question", state)


@router.post("/office/clear")
def clear_my_cases(request: Request):
    """Drop the signed browser session. Cases are not stored on the server."""
    return _redirect("/cases", clear=True)


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


def _case_context(decision, *, editable: bool, stepper_path: str, print_href: str, review: int, nav: str) -> dict:
    status = board_status_for(decision)
    total = len(decision.answers)
    review = min(max(review, 1), total) if total else 1
    return {
        "nav": nav,
        "decision": decision,
        "board_status": status,
        "chip_class": chip_class(status),
        "editable": editable,
        "stepper_path": stepper_path,
        "print_href": print_href,
        "review": review,
        "review_total": total,
        "review_answer": decision.answers[review - 1] if total else None,
        "snapshot": _snapshot(decision, status) if editable else None,
        "demo": None,
    }


@router.get("/office/result", response_class=HTMLResponse)
def result(request: Request):
    decision, early = _finished(request)
    if early is not None:
        return early
    context = _case_context(
        decision,
        editable=True,
        stepper_path="/office/result",
        print_href="/office/print",
        review=_review_number(request, len(decision.answers)),
        nav="check",
    )
    context["step"] = 3
    return _render(request, "decision.html", context)


@router.get("/office/print", response_class=HTMLResponse)
def print_summary(request: Request):
    decision, early = _finished(request)
    if early is not None:
        return early
    return _render(
        request,
        "summary.html",
        {
            "step": 4,
            "decision": decision,
            "nav": "check",
            "board_status": board_status_for(decision),
            "chip_class": chip_class(board_status_for(decision)),
        },
    )


@router.get("/cases/{case_id}", response_class=HTMLResponse)
def demo_case(case_id: str, request: Request):
    card = demo_card(case_id)
    if card is None:
        return _error(request, "That synthetic demo case is not on the board.", 404)
    decision = card["decision"]
    context = _case_context(
        decision,
        editable=False,
        stepper_path=f"/cases/{case_id}",
        print_href=f"/cases/{case_id}/print",
        review=_review_number(request, len(decision.answers)),
        nav="cases",
    )
    context.update(
        {
            "demo": card,
            "checked": card["checklist_checked"],
        }
    )
    return _render(request, "demo_case.html", context)


@router.get("/cases/{case_id}/print", response_class=HTMLResponse)
def demo_print(case_id: str, request: Request):
    card = demo_card(case_id)
    if card is None:
        return _error(request, "That synthetic demo case is not on the board.", 404)
    decision = card["decision"]
    return _render(
        request,
        "summary.html",
        {
            "step": 4,
            "decision": decision,
            "nav": "cases",
            "board_status": card["board_status"],
            "chip_class": card["chip_class"],
        },
    )
