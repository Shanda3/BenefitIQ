"""Server-rendered UI and JSON API."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select

from benefitiq.constants import DISCLAIMER
from benefitiq.config import samples_dir
from benefitiq.db import session_scope
from benefitiq.engine.recommend import evaluate
from benefitiq.models import Source
from benefitiq.schemas import CaseInput

app = FastAPI(title="BenefitIQ", version="0.1.0")

_UI = Path(__file__).resolve().parent / "ui"
templates = Jinja2Templates(directory=str(_UI / "templates"))
app.mount("/static", StaticFiles(directory=str(_UI / "static")), name="static")

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


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"disclaimer": DISCLAIMER, "samples": _sample_ids()},
    )


@app.get("/sample/{sample_id}", response_class=HTMLResponse)
def sample_result(sample_id: str, request: Request):
    try:
        case = CaseInput.model_validate(load_sample(sample_id))
    except (FileNotFoundError, ValidationError, json.JSONDecodeError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"disclaimer": DISCLAIMER, "message": "Unknown synthetic sample."},
            status_code=404,
        )
    return _render_result(request, evaluate(case))


@app.post("/evaluate", response_class=HTMLResponse)
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
                "message": "Case JSON was not accepted. Submit a synthetic case only.",
            },
            status_code=422,
        )
    return _render_result(request, evaluate(case))


@app.post("/api/evaluate")
def evaluate_api(case: CaseInput):
    return evaluate(case)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=422,
            content={"disclaimer": DISCLAIMER, "detail": "invalid synthetic case"},
        )
    return await request_validation_exception_handler(request, exc)
