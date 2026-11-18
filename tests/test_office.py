"""Office-manager flow: question branches, citations, banner, print, and basics."""

from __future__ import annotations

import logging
import re
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from benefitiq.constants import DISCLAIMER
from benefitiq.db import session_scope
from benefitiq.ingestion.robots import allows
from benefitiq.labels import load_labels
from benefitiq.main import app
from benefitiq.models import Chunk, Source
from benefitiq.office.decide import cite, decide
from benefitiq.office.tree import RESULT_LABELS, load_tree
from benefitiq.retrieval.search import CorpusRepo
from tests.conftest import ROOT

AS_OF = date(2026, 11, 18)
CASE = "SYN-OFFICE-TEST0001"
CPT_RE = re.compile(r"^[0-9]{4}[0-9A-Z]$")

SLEEP_YES = {
    "sleep_test": "yes",
    "osa_diagnosis": "yes",
    "practitioner_order": "yes",
    "custom_device": "yes",
    "product_list": "yes",
}
DENTAL_BILL = {
    "linked_service": "yes",
    "care_coordination": "yes",
    "routine_impacted": "no",
    "replacement_device": "no",
}

CMS_ROBOTS = """
User-agent: *
Allow: /medicare-coverage-database/*?
Disallow: /*?
"""


def _decide_sleep(**updates):
    answers = dict(SLEEP_YES)
    answers.update(updates)
    return decide("sleep_appliance", answers, as_of=AS_OF, case_id=CASE)


def _decide_dental(**updates):
    answers = dict(DENTAL_BILL)
    answers.update(updates)
    return decide("medicare_dental", answers, as_of=AS_OF, case_id=CASE)


@pytest.mark.parametrize(
    ("updates", "label", "rule_id"),
    [
        ({}, "Bill medical first", "sleep-bill"),
        ({"sleep_test": "no"}, "Probably not medical", "sleep-no-sleep-test"),
        ({"osa_diagnosis": "no"}, "Probably not medical", "sleep-no-diagnosis"),
        ({"practitioner_order": "no"}, "Probably not medical", "sleep-no-order"),
        ({"custom_device": "no"}, "Probably not medical", "sleep-no-custom"),
        ({"product_list": "no"}, "Probably not medical", "sleep-no-product-list"),
        ({"sleep_test": "not_sure"}, "Need more info", "sleep-not-sure"),
        (
            {key: "not_sure" for key in SLEEP_YES},
            "Need more info",
            "sleep-not-sure",
        ),
        (
            {"sleep_test": "not_sure", "custom_device": "no"},
            "Probably not medical",
            "sleep-no-custom",
        ),
    ],
)
def test_sleep_result_branches(updates, label, rule_id):
    result = _decide_sleep(**updates)
    assert result.result_label == label
    assert result.rule_id == rule_id
    assert result.result_label in RESULT_LABELS.values()
    if label == "Probably not medical":
        assert result.reason.text.startswith("Do not bill medical")
        assert all(code.status == "not_recommended" for code in result.codes if code.code)
    elif label == "Need more info":
        assert "not ready to bill" in result.reason.text
        assert any(gap.text.startswith("documentation missing:") for gap in result.gaps)
        assert all(code.status == "not_recommended" for code in result.codes)
    else:
        assert result.reason.text != "not found"
        assert {code.code for code in result.codes} == {"E0486", "G47.33"}
        assert all(code.status == "consider" for code in result.codes)
        assert all(code.label and code.label.startswith("placeholder:") for code in result.codes)
        assert result.gaps == []


def test_prefab_device_is_not_payable_and_other_nos_do_not_list_it():
    refused = _decide_sleep(custom_device="no")
    assert any(code.code == "E0485" and code.label == "placeholder: device-e0485" for code in refused.codes)
    assert "E0485" in refused.reason.text
    clean = _decide_sleep(sleep_test="no")
    assert all(code.code != "E0485" for code in clean.codes)


@pytest.mark.parametrize(
    ("updates", "label", "rule_id"),
    [
        ({}, "Bill medical first", "dental-bill"),
        ({"linked_service": "no"}, "Probably not medical", "dental-not-linked"),
        ({"routine_impacted": "yes", "linked_service": "no"}, "Probably not medical", "dental-routine"),
        (
            {"routine_impacted": "yes", "linked_service": "yes"},
            "Need more info",
            "dental-conflict",
        ),
        ({"replacement_device": "yes"}, "Probably not medical", "dental-replacement"),
        ({"care_coordination": "no"}, "Need more info", "dental-missing"),
        ({"linked_service": "not_sure"}, "Need more info", "dental-missing"),
        (
            {"replacement_device": "yes", "linked_service": "not_sure"},
            "Probably not medical",
            "dental-replacement",
        ),
        (
            {key: "not_sure" for key in DENTAL_BILL},
            "Need more info",
            "dental-missing",
        ),
    ],
)
def test_dental_result_branches(updates, label, rule_id):
    result = _decide_dental(**updates)
    assert result.result_label == label
    assert result.rule_id == rule_id
    if label == "Probably not medical":
        assert result.reason.text.startswith("Do not bill medical")
    elif label == "Need more info":
        assert result.reason.text != "not found"
        assert "not ready to bill" in result.reason.text or "not ready to bill" in result.checklist[0].text or "disagree" in result.reason.text
    else:
        assert any("modifier KX" in item.text for item in result.checklist)
        assert any(
            citation.effective_from == date(2025, 7, 1) and citation.status == "cited"
            for item in result.checklist
            for citation in item.citations
        )
    assert all(code.status == "not_found" for code in result.codes)
    assert all(code.code is None for code in result.codes)


def test_missing_coordination_is_documentation_missing():
    result = _decide_dental(care_coordination="no")
    assert result.result_label == "Need more info"
    assert any("documentation missing:" in gap.text for gap in result.gaps)


def test_incomplete_answers_do_not_invent_a_reason():
    result = decide("sleep_appliance", {}, as_of=AS_OF, case_id=CASE)
    assert result.result_label == "Need more info"
    assert result.reason.text == "not found"


def test_every_tree_citation_resolves_inside_the_effective_window():
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "source_id" in node and "section" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(load_tree())
    assert len(found) > 10
    with session_scope() as session:
        repo = CorpusRepo(session)
        for spec in found:
            view = cite(repo, spec["source_id"], spec["section"], AS_OF)
            assert view.status == "cited", spec
            assert view.url.startswith("https://www.cms.gov/")
            assert view.effective_from is not None
            assert "localcoverage.cms.gov" not in view.url


def test_unknown_source_and_out_of_window_source_are_not_found():
    with session_scope() as session:
        repo = CorpusRepo(session)
        missing = cite(repo, "not-a-real-source", "missing", AS_OF)
        assert missing.status == "not_found"
        early = cite(repo, "cms-lcd-l33611", "custom-device", date(2020, 1, 1))
        assert early.status == "not_found"

    result = decide("sleep_appliance", dict(SLEEP_YES), as_of=date(2020, 1, 1), case_id=CASE)
    assert result.result_label == "Bill medical first"
    assert result.reason.text == "not found"
    assert all(code.note == "not found" for code in result.codes)
    assert all(item.text == "not found" for item in result.checklist)
    joined = result.reason.text + " ".join(code.note for code in result.codes)
    assert "https://" not in joined


def test_office_source_urls_match_the_robots_allow_rule():
    manifest = yaml.safe_load((ROOT / "corpus" / "office" / "manifest.yaml").read_text(encoding="utf-8"))
    urls = [item["url"] for item in manifest["documents"]]
    assert len(urls) == 4
    for url in urls:
        assert "localcoverage.cms.gov" not in url
        assert allows(url, CMS_ROBOTS)


def test_question_counts_titles_and_no_cpt_codes():
    procedures = load_tree()["procedures"]
    by_id = {item["id"]: item for item in procedures}
    assert [item["title"] for item in procedures if item["active"]] == [
        "Sleep apnea oral appliance",
        "Dental care needed before a medical treatment (Medicare)",
    ]
    assert [item["title"] for item in procedures if not item["active"]] == [
        "TMJ",
        "Injury/trauma",
        "Biopsy",
        "CBCT scan",
    ]
    assert len(by_id["sleep_appliance"]["questions"]) == 5
    assert len(by_id["medicare_dental"]["questions"]) == 4

    def walk(node):
        if isinstance(node, dict):
            assert node.get("system") != "CPT"
            code = node.get("code")
            if isinstance(code, str):
                assert CPT_RE.fullmatch(code) is None
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(procedures)
    labels = load_labels()
    for code in ("E0485", "E0486", "G47.33"):
        assert labels[code].startswith("placeholder:")


def test_repository_has_no_banned_payment_language():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".yml", ".yaml", ".html", ".css", ".txt"}:
            continue
        if ("maximize " + "reimbursement") in path.read_text(encoding="utf-8").lower():
            offenders.append(str(path))
    assert offenders == []


@pytest.fixture
def browser():
    return TestClient(app, follow_redirects=True)


def _walk(browser: TestClient, procedure_id: str, choices: list[str]):
    started = browser.post("/office/start", data={"procedure_id": procedure_id})
    assert started.status_code == 200, started.text
    page = started
    for choice in choices:
        page = browser.post("/office/answer", data={"choice": choice})
        assert page.status_code == 200, page.text
    return page


def test_banner_is_on_every_html_screen(monkeypatch, browser):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    pages = [
        browser.get("/"),
        browser.get("/advanced"),
        browser.post("/advanced/evaluate", data={"sample_id": "trauma"}),
        browser.get("/advanced/sample/biopsy"),
        browser.get("/advanced/sample/missing-sample"),
        browser.get("/not-a-page"),
    ]
    _walk(browser, "sleep_appliance", ["yes"] * 5)
    pages.extend(
        [
            browser.get("/office/question"),
            browser.get("/office/result"),
            browser.get("/office/print"),
        ]
    )
    # The finished session redirects /office/question to the result. Start a new one.
    browser.post("/office/start", data={"procedure_id": "medicare_dental"})
    pages.append(browser.get("/office/question"))
    for page in pages:
        assert page.status_code in {200, 404}
        assert DISCLAIMER in page.text
        assert 'data-testid="decision-support-banner"' in page.text
        assert ("maximize " + "reimbursement") not in page.text.lower()


def test_sleep_path_result_and_print_summary(monkeypatch, browser):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    page = _walk(browser, "sleep_appliance", ["yes"] * 5)
    assert page.status_code == 200
    assert 'data-testid="result-phrase">Bill medical first' in page.text
    assert "placeholder: airway-device" in page.text
    assert "placeholder: sleep-dx" in page.text
    assert "E0486" in page.text
    assert "G47.33" in page.text
    assert "https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=33611" in page.text
    assert "2021-08-08" in page.text
    assert "Hold the dental claim until the medical explanation of benefits arrives." not in page.text
    assert "not found" in page.text
    printed = browser.get("/office/print")
    assert printed.status_code == 200
    assert 'data-testid="print-summary"' in printed.text
    assert "Bill medical first" in printed.text
    assert "SYN-OFFICE-" in printed.text
    assert "2026-11-18" in printed.text
    assert "Has the patient had a qualifying sleep test?" in printed.text
    assert ">Yes<" in printed.text
    assert "Checklist" in printed.text
    assert "https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=52512" in printed.text
    assert DISCLAIMER in printed.text


def test_dental_path_can_say_do_not_bill_medical(monkeypatch, browser):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    page = _walk(browser, "medicare_dental", ["yes", "yes", "no", "yes"])
    assert "Probably not medical" in page.text
    assert "Do not bill medical" in page.text
    assert "not found" in page.text
    assert "https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c15.pdf" in page.text


def test_back_button_returns_to_the_previous_question(monkeypatch, browser):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    browser.post("/office/answer", data={"choice": "yes"})
    back = browser.post("/office/back")
    assert back.status_code == 200
    assert "Question 1 of 5" in back.text
    assert "qualifying sleep test" in back.text
    assert "Previous answer: Yes" in back.text


def test_coming_soon_tiles_are_disabled_and_rejected(browser):
    home = browser.get("/")
    assert home.text.count("Coming soon") == 4
    assert 'aria-disabled="true"' in home.text
    assert 'disabled' in home.text
    refused = browser.post("/office/start", data={"procedure_id": "tmj"})
    assert refused.status_code == 400
    assert DISCLAIMER in refused.text
    assert "not available yet" in refused.text


def test_accessibility_basics_on_the_main_screens(monkeypatch, browser):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    home = browser.get("/")
    browser.post("/office/start", data={"procedure_id": "sleep_appliance"})
    question = browser.get("/office/question")
    browser.post("/office/answer", data={"choice": "yes"})
    browser.post("/office/answer", data={"choice": "yes"})
    browser.post("/office/answer", data={"choice": "yes"})
    browser.post("/office/answer", data={"choice": "yes"})
    browser.post("/office/answer", data={"choice": "yes"})
    result = browser.get("/office/result")
    printed = browser.get("/office/print")
    advanced = browser.get("/advanced")
    css = (ROOT / "benefitiq" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "@media print" in css
    assert ":focus-visible" in css
    assert "min-height: 3.25rem" in css
    for page in (home, question, result, printed, advanced):
        html = page.text
        assert page.status_code == 200
        assert "<html lang=\"en\"" in html
        assert 'name="viewport"' in html
        assert html.count("<h1") == 1
        assert "<main" in html
        assert 'href="#main"' in html
        assert re.search(r"<button[^>]*>\s*</button>", html) is None
        assert DISCLAIMER in html
    assert 'data-testid="question-progress"' in question.text
    assert "Question 1 of 5" in question.text
    assert question.text.count(">Yes<") == 1
    assert question.text.count(">No<") == 1
    assert question.text.count(">Not sure<") == 1
    assert ">Back<" in question.text
    for tile in ("Sleep apnea oral appliance", "TMJ", "Injury/trauma", "Biopsy", "CBCT scan"):
        assert tile in home.text


def test_flow_does_not_persist_the_case(monkeypatch, browser):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    with session_scope() as session:
        before = session.scalar(select(func.count()).select_from(Source))
    page = _walk(browser, "sleep_appliance", ["no", "yes", "yes", "yes", "yes"])
    case_id = re.search(r"SYN-OFFICE-[0-9A-F]{8}", page.text)
    assert case_id
    with session_scope() as session:
        after = session.scalar(select(func.count()).select_from(Source))
        bodies = session.scalars(select(Chunk.body)).all()
    assert before == after
    assert case_id.group(0) not in "\n".join(bodies)


def test_audit_log_has_case_id_and_source_ids_only(caplog):
    prompt = "Has the patient had a qualifying sleep test?"
    with caplog.at_level(logging.INFO, logger="benefitiq.audit"):
        result = _decide_sleep()
    assert f"office_result case_id={CASE}" in caplog.text
    assert "cms-lcd-l33611" in caplog.text
    assert "cms-article-a52512" in caplog.text
    assert prompt not in caplog.text
    assert result.reason.text not in caplog.text
    assert "not_sure" not in caplog.text


def test_tampered_session_is_ignored(browser):
    browser.cookies.set("benefitiq_office", "aaaa.bbbb")
    response = browser.get("/office/question", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
