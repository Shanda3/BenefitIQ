"""Oklahoma payer registry, access status, and plan filtering."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from benefitiq.constants import DISCLAIMER
from benefitiq.ingestion.payer_fetch import download
from benefitiq.ingestion.policy_text import redact_policy_text
from benefitiq.ingestion.robots import allows
from benefitiq.ingestion.service import ingest_manifest
from benefitiq.main import app
from benefitiq.office.present import payer_gap
from benefitiq.payers.catalog import (
    PENDING_ACCESS,
    blocked_fetch_reason,
    citable_sources,
    enrollment,
    fetchable_sources,
    registry_counts,
    skipped_sources,
    sources,
)
from benefitiq.payers.build import CARDS, MANIFEST, card_text
from benefitiq.payers.dates import parse_source_date
from benefitiq.payers.scope import plan_scope

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

ALLOWED_FIGURES = {
    3959400,
    1749000,
    842400,
    600700,
    231300,
    72100,
    463900,
    378422,
    158841,
    281488,
    261887,
    147,
    3125,
    77,
    571860,
    345724,
    298062,
    143200,
    161543,
    72,
    8174,
    38346,
    10517,
    15633,
    25,
    27,
    37658,
    21100,
    5032,
    15,
    57,
    143591,
    25207,
    14504,
    64594,
    21,
    43,
    14,
    12,
    19239,
    117184,
    197194,
    191509,
    18091,
    183157,
    341016,
    270380,
    2602,
    2039,
    492,
}

ALLOWED_SHARES = {"44.2%", "21.3%", "15.2%", "5.8%", "1.8%", "11.7%", "79%", "90%", "57%", "2.4%", "10%", "7%", "6%", "10.9%", "3%", "41.5%", "14%", "4.2%", "23%", "5.6%", "33.9%", "0.8%", "0.6%", "0.1%"}


def _figures():
    found = set()
    shares = set()
    data = enrollment()
    coverage = data["state_coverage"]
    found.add(coverage["total"])
    for row in coverage["rows"]:
        found.add(row["people"])
        shares.add(row["share"])
    for market in data["markets"]:
        for key in ("total", "plan_ids", "plan_county_rows", "counties"):
            if key in market:
                found.add(market[key])
    for payer in data["payers"]:
        for segment in payer["segments"]:
            for figure in segment.get("figures") or []:
                if "value" in figure:
                    found.add(figure["value"])
                if figure.get("share"):
                    shares.add(figure["share"])
    return found, shares


def test_every_source_row_is_added_already_or_skipped():
    counts = registry_counts()
    assert counts["csv_rows"] == 102
    assert counts["added"] == 97
    assert counts["already"] == 4
    assert counts["skipped"] == 1
    assert counts["citable"] == 77
    assert counts["fetch"] == 69
    assert counts["pending"] == 7
    assert counts["licensed"] == 4
    assert counts["login"] == 1
    assert counts["unverified"] == 1
    assert counts["not_applicable"] == 1
    assert {item.disposition for item in sources()} <= {"added", "already"}
    assert len(skipped_sources()) == 1
    skipped = skipped_sources()[0]
    assert "medicalpolicy.bcbsok.com" in skipped.url
    assert "terms-gated" in skipped.reason


def test_humana_is_pending_and_not_fetched():
    humana = [item for item in sources() if "humana.com" in item.url]
    assert humana
    assert {item.access for item in humana} == {PENDING_ACCESS}
    assert all(item.fetch is False for item in humana)
    assert blocked_fetch_reason(humana[0].url)


def test_excluded_addresses_are_not_fetched():
    urls = [item.url for item in fetchable_sources()]
    assert not any("medicalpolicy.bcbsok.com" in url for url in urls)
    assert not any("connections.cigna.com" in url for url in urls)
    assert not any("mcp.humana.com" in url for url in urls)
    assert not any("provideraccess.dentaquest.com" in url for url in urls)
    assert not any("manuals.health.mil" in url or "manuals.dha.mil" in url for url in urls)
    for url in urls:
        host = url.lower()
        if "ccok.com" in host or "ccokadvantage.com" in host:
            assert "/pdf/" not in host
            assert "/assets/" not in host
    palmetto = [item for item in sources() if item.number == "A59449"]
    assert len(palmetto) == 1
    assert palmetto[0].access == "not_applicable"
    assert palmetto[0].fetch is False
    tricare = [item for item in sources() if "manuals." in item.url]
    assert tricare
    assert {item.access for item in tricare} == {"unverified"}
    assert all(item.fetch is False for item in tricare)


def test_bcbsok_gaps_and_sleep_policies():
    scope = plan_scope("bcbsok", "large_group")
    gaps = {topic.topic_id for topic in scope.topics if topic.notice == payer_gap(scope.payer_name)}
    assert {"frenectomy", "biopsy", "cbct", "anesthesia", "accidental"} <= gaps
    sleep = plan_scope("bcbsok", "large_group", "sleep_appliance")
    numbers = {hit.source.number for topic in sleep.topics for hit in topic.policies}
    assert numbers == {"MED204.006", "MED204.005"}
    assert sleep.uncited_sentence == (
        "Policies on file for Blue Cross and Blue Shield of Oklahoma. "
        "Confirm them with the payer before billing."
    )


def test_enrollment_figures_come_from_the_notes():
    found, shares = _figures()
    assert found <= ALLOWED_FIGURES
    assert shares <= ALLOWED_SHARES
    assert 772 not in found
    text = (ROOT / "corpus" / "payers" / "enrollment.yaml").read_text(encoding="utf-8")
    assert "about 187,000" in text
    assert "772" not in text


def test_review_dates_keep_their_label():
    reviewed = parse_source_date("Last Review 01/02/2026")
    assert reviewed.basis == "last_review"
    assert reviewed.value.isoformat() == "2026-01-02"
    assert not reviewed.label.lower().startswith("effective")
    missing = parse_source_date("not found")
    assert missing.value is None


def test_registry_cards_do_not_copy_descriptors():
    cards = list(CARDS.glob("*.md"))
    assert len(cards) == len(citable_sources())
    for source in citable_sources():
        text = (CARDS / f"{source.source_id}.md").read_text(encoding="utf-8")
        assert text == card_text(source)
        assert "CPT" not in text
        assert not re.search(r"\bD\d{4}\b", text)
        assert not any(re.fullmatch(r"\d{5}", line.strip()) for line in text.splitlines())
    redacted = redact_policy_text("Use 41899 for the service.\nCode D7240 extraction of tooth.\nThe plan lists a date.")
    assert "41899" not in redacted
    assert "extraction" not in redacted
    assert "D7240" in redacted
    assert "The plan lists a date." in redacted


def test_payer_manifest_is_not_overwritten():
    first = ingest_manifest(MANIFEST)
    assert first["skipped"] == len(citable_sources())
    assert first["inserted"] == 0


def test_ambetter_robots_allow_pdfs():
    body = "User-agent: *\nDisallow: /content/\nAllow: /content/*.pdf\nAllow: /content/dam/\n"
    assert allows("https://www.ambetterhealth.com/content/policy.pdf", body) is True
    assert allows("https://www.ambetterhealth.com/content/index.html", body) is False


def test_download_stops_when_robots_disallow(tmp_path, monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        class Response:
            def geturl(self):
                return request.full_url

            def read(self, _n=None):
                return b"User-agent: *\nDisallow: /\n"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Response()

    monkeypatch.setattr("benefitiq.ingestion.payer_fetch.urlopen", fake_urlopen)
    with pytest.raises(OSError, match="robots.txt disallows"):
        download("https://example.test/policy.pdf", tmp_path / "policy.bin", {})


def _shown(html: str) -> str:
    return html.replace("&#39;", "'")


def test_payer_pages_and_plan_filter(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    directory = client.get("/payers")
    assert directory.status_code == 200
    assert "<h1>Payers</h1>" in directory.text
    assert "Blue Cross and Blue Shield of Oklahoma" in directory.text
    assert DISCLAIMER in directory.text
    assert directory.text.count("<h1") == 1

    detail = client.get("/payers/bcbsok?segment=large_group")
    assert detail.status_code == 200
    assert payer_gap("Blue Cross and Blue Shield of Oklahoma") in _shown(detail.text)
    assert "MED204.006" in detail.text
    assert "36 O.S." in detail.text
    assert "298,062" in detail.text

    procedures = client.get("/procedures?plan=bcbsok:large_group")
    assert procedures.status_code == 200
    assert procedures.text.count("Coming soon") == 8
    assert payer_gap("Blue Cross and Blue Shield of Oklahoma") in _shown(procedures.text)
    assert "<form" not in client.get("/sources").text

    plain = client.get("/procedures")
    assert plain.text.count("Coming soon") == 8


def _finish(plan: str):
    browser = TestClient(app, follow_redirects=True)
    started = browser.post("/office/start", data={"procedure_id": "sleep_appliance", "plan": plan})
    assert started.status_code == 200
    for _ in range(5):
        browser.post("/office/answer", data={"choice": "yes"})
    return browser.get("/office/result")


def test_bcbsok_case_lists_plan_policies_and_drops_medicare(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    page = _finish("bcbsok:large_group")
    assert page.status_code == 200
    assert "Confirm with the payer" in page.text
    assert "MED204.006" in page.text
    assert "MED204.005" in page.text
    assert "Policies on file for Blue Cross and Blue Shield of Oklahoma" in _shown(page.text)
    assert "Our sources don't cover this yet" not in _shown(page.text)
    assert "L33611" not in page.text
    assert "Bill medical first" not in page.text


def test_soonerselect_case_says_the_library_has_no_policy(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    page = _finish("och:soonerselect_medical")
    assert payer_gap("Oklahoma Complete Health") in _shown(page.text)
    assert "CP.MP.248" not in page.text
    assert "L33611" not in page.text
    assert "Bill medical first" not in page.text


def test_aetna_better_health_names_licensed_criteria(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    page = _finish("aetna-better-health:soonerselect_medical")
    assert "This payer's criteria aren't public. Confirm with the payer." in _shown(page.text)
    assert "CPB 0004" in page.text


def test_no_plan_still_uses_medicare_rules(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    page = _finish("")
    assert "Bill medical first" in page.text
    assert "L33611" in page.text or "cms-lcd-l33611" in page.text
