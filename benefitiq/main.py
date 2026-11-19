"""Server-rendered UI and JSON API."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from benefitiq.constants import DISCLAIMER
from benefitiq.config import samples_dir
from benefitiq.db import session_scope
from benefitiq.engine.recommend import evaluate
from benefitiq.models import Source
from benefitiq.ingestion.policy_text import excerpt
from benefitiq.office.present import plain_gap
from benefitiq.office.routes import router as office_router
from benefitiq.retrieval.search import CorpusRepo
from benefitiq.schemas import CaseInput

app = FastAPI(title="BenefitIQ", version="0.1.0")

_UI = Path(__file__).resolve().parent / "ui"
templates = Jinja2Templates(directory=str(_UI / "templates"))
templates.env.filters["plain_gap"] = plain_gap
templates.env.filters["access_chip"] = lambda label: (
    "chip-ready"
    if label == "Public"
    else "chip-docs"
    if label in {"Pending document access", "Web address not verified"}
    else "chip-none"
)
app.mount("/static", StaticFiles(directory=str(_UI / "static")), name="static")
app.include_router(office_router)

_SAMPLE_ID = re.compile(r"^[a-z0-9_]+$")


def _sample_ids() -> list[str]:
    directory = samples_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def load_sample(sample_id: str) -> dict:
    if not _SAMPLE_ID.fullmatch(sample_id):
        raise FileNotFoundError(sample_id)
    path = samples_dir() / f"{sample_id}.json"
    if not path.is_file():
        raise FileNotFoundError(sample_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _render_result(request: Request, result) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "disclaimer": DISCLAIMER,
            "result": result,
            "samples": _sample_ids(),
            "nav": "advanced",
        },
    )


@app.get("/health")
def health():
    try:
        with session_scope() as session:
            count = session.scalar(select(func.count()).select_from(Source))
    except Exception:  # noqa: BLE001 - health check must not leak connection details
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok", "sources": int(count or 0)}


def _retrieval_debug(query: str, payer: str, segment: str, topic: str) -> dict | None:
    cleaned = query.strip()
    if not cleaned:
        return None
    with session_scope() as session:
        repo = CorpusRepo(session)
        hits = repo.hybrid_search(
            cleaned,
            dos=date.today(),
            payer=payer.strip() or None,
            segment=segment.strip() or None,
            topic=topic.strip() or None,
            limit=8,
        )
        rows = []
        for hit in hits:
            record = repo.get_record(hit.source_id, hit.chunk_id)
            keyword = float(hit.keyword_score or 0)
            vector = float(hit.vector_score or 0)
            rows.append(
                {
                    "title": hit.title,
                    "source_id": hit.source_id,
                    "chunk_id": hit.chunk_id,
                    "section": hit.section_anchor,
                    "url": hit.url,
                    "effective_from": hit.effective_from,
                    "payer": record.payer if record else "",
                    "keyword_score": f"{keyword:.3f}",
                    "vector_score": f"{vector:.3f}",
                    "quote": excerpt(record.text) if record else "",
                    "relevant": keyword > 0 or vector >= 0.55,
                }
            )
    relevant = [row for row in rows if row["relevant"]]
    return {"query": cleaned, "rows": rows, "relevant": relevant}


@app.get("/advanced", response_class=HTMLResponse)
def advanced_index(
    request: Request,
    q: str = "",
    payer: str = "",
    segment: str = "",
    topic: str = "",
):
    return templates.TemplateResponse(
        request,
        "advanced.html",
        {
            "disclaimer": DISCLAIMER,
            "samples": _sample_ids(),
            "nav": "advanced",
            "debug": _retrieval_debug(q, payer, segment, topic),
            "q": q,
            "payer": payer,
            "segment": segment,
            "topic": topic,
        },
    )


@app.get("/advanced/sample/{sample_id}", response_class=HTMLResponse)
def sample_result(sample_id: str, request: Request):
    try:
        case = CaseInput.model_validate(load_sample(sample_id))
    except (FileNotFoundError, ValidationError, json.JSONDecodeError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "disclaimer": DISCLAIMER,
                "heading": "Please try again",
                "message": "Unknown synthetic sample.",
                "back_href": "/advanced",
                "back_label": "Back to the engineer view",
                "nav": "advanced",
            },
            status_code=404,
        )
    return _render_result(request, evaluate(case))


@app.post("/advanced/evaluate", response_class=HTMLResponse)
def evaluate_form(
    request: Request,
    case_json: str | None = Form(default=None),
    sample_id: str | None = Form(default=None),
):
    try:
        if sample_id:
            payload = load_sample(sample_id)
            case = CaseInput.model_validate(payload)
        else:
            case = CaseInput.model_validate_json(case_json or "")
    except (FileNotFoundError, ValidationError, json.JSONDecodeError, ValueError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "disclaimer": DISCLAIMER,
                "heading": "Please try again",
                "message": "Case JSON was not accepted. Submit a synthetic case only.",
                "back_href": "/advanced",
                "back_label": "Back to the engineer view",
                "nav": "advanced",
            },
            status_code=422,
        )
    return _render_result(request, evaluate(case))


@app.post("/api/evaluate")
def evaluate_api(case: CaseInput):
    return evaluate(case)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "disclaimer": DISCLAIMER,
                "heading": "Please try again",
                "message": "That page is not available.",
                "back_href": "/",
                "back_label": "Back to the start",
                "nav": "",
            },
            status_code=404,
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=422,
            content={"disclaimer": DISCLAIMER, "detail": "invalid synthetic case"},
        )
    return await request_validation_exception_handler(request, exc)
