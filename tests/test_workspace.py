"""Workspace UI: board statuses, banner, plain gaps, chat, print, and basics."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from benefitiq.constants import DISCLAIMER
from benefitiq.db import session_scope
from benefitiq.main import app
from benefitiq.models import Chunk, Source
from benefitiq.office.demo import SYNTHETIC_LABEL, demo_cards
from benefitiq.office.present import (
    BOARD_COLUMNS,
    NEEDS_DOCS,
    NOT_COVERED,
    PLAIN_GAP,
    READY,
    board_status,
    plain_gap,
)
from tests.conftest import ROOT

client = TestClient(app)
CSS = (ROOT / "benefitiq" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
JS = (ROOT / "benefitiq" / "ui" / "static" / "workspace.js").read_text(encoding="utf-8")


def _contrast(left: str, right: str) -> float:
    def luminance(hex_color: str) -> float:
        def channel(value: int) -> float:
            unit = value / 255
            return unit / 12.92 if unit <= 0.04045 else ((unit + 0.055) / 1.055) ** 2.4

        red = int(hex_color[0:2], 16)
        green = int(hex_color[2:4], 16)
        blue = int(hex_color[4:6], 16)
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    lighter, darker = sorted((luminance(left), luminance(right)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _css_color(name: str) -> str:
    match = re.search(rf"--{name}:\s*#([0-9a-fA-F]{{6}})", CSS)
    assert match, name
    return match.group(1)


def _shown(html: str) -> str:
    """Jinja escapes apostrophes. The rendered sentence is unchanged."""
    return html.replace("&#39;", "'")


def _column(html: str, testid: str, end: str) -> str:
    return html.split(f'data-testid="{testid}"', 1)[1].split(f'data-testid="{end}"', 1)[0]


def test_board_status_mapping():
    assert plain_gap("not found") == PLAIN_GAP
    assert plain_gap("Code to consider.") == "Code to consider."
    assert board_status("Bill medical first") == READY
    assert board_status("Bill medical first", documentation_missing=True) == NEEDS_DOCS
    assert board_status("Bill medical first", uncited=True) == NOT_COVERED
    assert board_status("Need more info") == NEEDS_DOCS
    assert board_status("Need more info", uncited=True) == NEEDS_DOCS
    assert board_status("Probably not medical") == NOT_COVERED
    assert board_status("Probably not medical", documentation_missing=True) == NEEDS_DOCS
    assert set(BOARD_COLUMNS) == {READY, NEEDS_DOCS, NOT_COVERED}


def test_demo_cases_map_onto_every_column_and_stay_out_of_the_database():
    cards = demo_cards()
    assert 6 <= len(cards) <= 8
    by_id = {card["case_id"]: card for card in cards}
    assert len(by_id) == len(cards)
    assert {card["board_status"] for card in cards} == set(BOARD_COLUMNS)
    assert by_id["SYN-DEMO-0001"]["board_status"] == READY
    assert by_id["SYN-DEMO-0001"]["result_label"] == "Bill medical first"
    assert by_id["SYN-DEMO-0003"]["board_status"] == NEEDS_DOCS
    assert by_id["SYN-DEMO-0003"]["result_label"] == "Need more info"
    assert by_id["SYN-DEMO-0006"]["board_status"] == NEEDS_DOCS
    assert by_id["SYN-DEMO-0002"]["board_status"] == NOT_COVERED
    assert by_id["SYN-DEMO-0002"]["result_label"] == "Probably not medical"
    assert by_id["SYN-DEMO-0007"]["board_status"] == NOT_COVERED
    assert by_id["SYN-DEMO-0008"]["result_label"] == "Bill medical first"
    assert by_id["SYN-DEMO-0008"]["board_status"] == NOT_COVERED
    assert by_id["SYN-DEMO-0008"]["reason_text"] == PLAIN_GAP
    for card in cards:
        assert card["case_id"].startswith("SYN-DEMO-")
        assert card["synthetic_label"] == SYNTHETIC_LABEL == "SYNTHETIC demo"
        assert card["checklist_checked"] <= card["checklist_total"]
        assert card["progress_label"] == f"{card['checklist_checked']} of {card['checklist_total']}"
    with session_scope() as session:
        bodies = "\n".join(session.scalars(select(Chunk.body)).all())
        stored = session.scalar(select(func.count()).select_from(Source))
    assert stored > 0
    for card in cards:
        assert card["case_id"] not in bodies


def test_cases_board_and_list_show_the_same_synthetic_cases():
    board = client.get("/cases")
    assert board.status_code == 200
    assert DISCLAIMER in board.text
    assert 'data-testid="decision-support-banner"' in board.text
    assert 'data-testid="cases-board"' in board.text
    assert "hidden" not in board.text.split('data-testid="cases-board"', 1)[1][:40]
    ready = _column(board.text, "column-ready", "column-docs")
    docs = _column(board.text, "column-docs", "column-none")
    uncovered = _column(board.text, "column-none", "cases-list")
    assert "SYN-DEMO-0001" in ready
    assert "SYN-DEMO-0004" in ready
    assert "SYN-DEMO-0003" in docs
    assert "SYN-DEMO-0006" in docs
    assert "SYN-DEMO-0002" in uncovered
    assert "SYN-DEMO-0008" in uncovered
    assert "SYNTHETIC demo" in board.text
    assert 'data-testid="clear-my-cases"' in board.text
    assert 'action="/office/clear"' in board.text
    assert re.search(r"\d+ of \d+", board.text)

    listed = client.get("/cases?view=list")
    assert listed.status_code == 200
    assert 'data-testid="cases-list"' in listed.text
    assert 'data-testid="cases-board" hidden' in listed.text
    assert "<table" in listed.text
    for case_id in ("SYN-DEMO-0001", "SYN-DEMO-0008"):
        assert case_id in listed.text
    assert DISCLAIMER in listed.text


def test_banner_is_present_on_every_workspace_page(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser = TestClient(app, follow_redirects=True)
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    pages = [
        client.get("/cases"),
        client.get("/cases?view=list"),
        client.get("/procedures"),
        client.get("/sources"),
        client.get("/help"),
        client.get("/cases/SYN-DEMO-0001"),
        client.get("/cases/SYN-DEMO-0008"),
        client.get("/cases/SYN-DEMO-0001/print"),
        client.get("/cases/local"),
        client.get("/cases/missing-demo"),
        browser.get("/office/question"),
        browser.get("/office/print"),
        client.get("/not-a-workspace-page"),
    ]
    for page in pages:
        assert page.status_code in {200, 404}
        assert DISCLAIMER in page.text
        assert 'data-testid="decision-support-banner"' in page.text
        assert page.text.count("<h1") == 1


def test_plain_language_gap_replaces_a_bare_not_found(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser = TestClient(app, follow_redirects=True)
    uncovered = client.get("/cases/SYN-DEMO-0008")
    assert uncovered.status_code == 200
    assert PLAIN_GAP in _shown(uncovered.text)
    assert ">not found<" not in uncovered.text
    assert 'data-internal-status="not found"' in uncovered.text

    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    for _ in range(5):
        browser.post("/office/answer", data={"choice": "yes"})
    result = browser.get("/office/result")
    assert result.status_code == 200
    assert PLAIN_GAP in _shown(result.text)
    assert ">not found<" not in result.text
    assert "not found" in result.text
    assert "Hold the dental claim until the medical explanation of benefits arrives." not in result.text
    printed = browser.get("/office/print")
    assert PLAIN_GAP in _shown(printed.text)
    assert ">not found<" not in printed.text


def test_chat_panel_is_disabled_and_makes_no_network_call(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser = TestClient(app, follow_redirects=True)
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    question = browser.get("/office/question")
    for _ in range(5):
        browser.post("/office/answer", data={"choice": "yes"})
    result = browser.get("/office/result")
    demo = client.get("/cases/SYN-DEMO-0001")
    for page in (question, result, demo):
        assert page.status_code == 200
        assert "Ask about this case (coming soon)" in page.text
        assert 'data-testid="case-chat"' in page.text
        assert 'id="case-chat-input"' in page.text
        textarea = re.search(r"<textarea[^>]*id=\"case-chat-input\"[^>]*>", page.text)
        assert textarea and "disabled" in textarea.group(0)
        assert 'aria-disabled="true"' in textarea.group(0)
        send = re.search(r"<button[^>]*>\s*Send\s*</button>", page.text)
        assert send and "disabled" in send.group(0)
        assert "openai" not in page.text.lower()
        assert "OPENAI_API_KEY" not in page.text
        assert "api.openai.com" not in page.text
        assert "fetch(" not in page.text
    lowered = JS.lower()
    for needle in ("fetch(", "xmlhttprequest", "websocket", "sendbeacon", "eventsource", "openai"):
        assert needle not in lowered
    missing = client.post("/api/chat", json={"message": "synthetic question"})
    assert missing.status_code == 404


def test_print_view_keeps_the_banner_and_the_summary(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser = TestClient(app, follow_redirects=True)
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    for _ in range(5):
        browser.post("/office/answer", data={"choice": "yes"})
    printed = browser.get("/office/print")
    assert printed.status_code == 200
    assert 'data-testid="print-summary"' in printed.text
    assert "Biller summary" in printed.text
    assert "Bill medical first" in printed.text
    assert "Checklist" in printed.text
    assert "Print this page" in printed.text
    assert "window.print()" in printed.text
    assert DISCLAIMER in printed.text
    assert 'data-testid="decision-support-banner"' in printed.text
    assert "@media print" in CSS
    print_css = CSS.split("@media print", 1)[1]
    assert ".banner" in print_css
    assert "display: block !important" in print_css
    assert ".sidebar" in print_css
    demo_print = client.get("/cases/SYN-DEMO-0004/print")
    assert demo_print.status_code == 200
    assert 'data-testid="print-summary"' in demo_print.text
    assert DISCLAIMER in demo_print.text
    assert "SYN-DEMO-0004" in demo_print.text


def test_sources_page_lists_registered_sources_and_is_read_only():
    page = client.get("/sources")
    assert page.status_code == 200
    assert "<h1>Sources</h1>" in page.text
    assert 'data-readonly="true"' in page.text
    assert "https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=33611" in page.text
    assert "2021-08-08" in page.text
    assert "https://www.cms.gov/medicare/coverage/dental" in page.text
    assert "2025-07-01" in page.text
    assert "<form" not in page.text
    assert DISCLAIMER in page.text


def test_clear_my_cases_drops_the_browser_session_only(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser = TestClient(app, follow_redirects=True)
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    assert browser.cookies.get("benefitiq_office")
    with session_scope() as session:
        before = session.scalar(select(func.count()).select_from(Source))
    cleared = browser.post("/office/clear", follow_redirects=False)
    assert cleared.status_code == 303
    assert cleared.headers["location"] == "/cases"
    cookie = cleared.headers.get("set-cookie", "")
    assert "benefitiq_office" in cookie
    with session_scope() as session:
        after = session.scalar(select(func.count()).select_from(Source))
    assert before == after
    assert "localStorage.removeItem" in JS
    assert "benefitiq.myCases.v1" in JS


def test_answers_stay_editable_after_the_result(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser = TestClient(app, follow_redirects=True)
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    for _ in range(5):
        browser.post("/office/answer", data={"choice": "yes"})
    result = browser.get("/office/result")
    assert 'data-testid="question-stepper"' in result.text
    assert "Question 1 of 5" in result.text
    edited = browser.post("/office/edit", data={"question_index": "0"})
    assert edited.status_code == 200
    assert "Question 1 of 5" in edited.text
    assert "Previous answer: Yes" in edited.text
    assert "qualifying sleep test" in edited.text


def test_procedures_library_lists_active_and_coming_soon():
    page = client.get("/procedures")
    assert page.status_code == 200
    assert "<h1>Procedures library</h1>" in page.text
    assert "Sleep apnea oral appliance" in page.text
    assert "Dental care needed before a medical treatment (Medicare)" in page.text
    for title in ("TMJ", "Injury/trauma", "Biopsy", "CBCT scan"):
        assert title in page.text
    assert page.text.count("Coming soon") == 8
    assert 'aria-disabled="true"' in page.text
    assert 'href="/advanced"' in page.text


def test_workspace_accessibility_basics():
    pages = [
        client.get("/cases"),
        client.get("/cases?view=list"),
        client.get("/procedures"),
        client.get("/sources"),
        client.get("/help"),
        client.get("/cases/SYN-DEMO-0001"),
        client.get("/"),
    ]
    assert ":focus-visible" in CSS
    assert "min-height: 3.25rem" in CSS
    assert "@media (max-width: 860px)" in CSS
    assert "translateX(-105%)" in CSS
    for page in pages:
        html = page.text
        assert page.status_code == 200
        assert '<html lang="en"' in html
        assert 'name="viewport"' in html
        assert html.count("<h1") == 1
        assert "<main" in html
        assert 'href="#main"' in html
        assert 'aria-label="Workspace"' in html
        assert 'href="/advanced"' in html
        assert re.search(r"<button[^>]*>\s*</button>", html) is None
        assert DISCLAIMER in html
    pairs = [
        ("ink", "card"),
        ("muted", "card"),
        ("banner-ink", "banner-bg"),
        ("ready-ink", "ready-bg"),
        ("docs-ink", "docs-bg"),
        ("none-ink", "none-bg"),
    ]
    for ink, surface in pairs:
        assert _contrast(_css_color(ink), _css_color(surface)) >= 4.5
    assert _contrast("ffffff", _css_color("accent")) >= 4.5


def test_application_code_does_not_call_a_model():
    roots = [ROOT / "benefitiq", ROOT / "questions", ROOT / "corpus", ROOT / "samples"]
    offenders = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".html", ".js", ".css", ".yml", ".yaml", ".json", ".md"}:
                continue
            text = path.read_text(encoding="utf-8").lower()
            if "openai" in text or "api.openai.com" in text:
                offenders.append(str(path))
    assert offenders == []
    plan = (ROOT / "docs" / "chat-endpoint-plan.md").read_text(encoding="utf-8")
    assert "POST /api/chat" in plan
    assert "OPENAI_API_KEY" in plan
    assert "Cloudflare" in plan
    assert "does not implement" in plan
    assert "Hyperdrive" in plan
