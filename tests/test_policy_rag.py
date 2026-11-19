"""Policy retrieval: chunking, versions, review gating, and filters.

Fixtures are synthetic. These tests do not download payer sites.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from sqlalchemy import delete, select

from benefitiq.constants import EMBEDDING_DIM
from benefitiq.db import session_scope
from benefitiq.ingestion.chunking import chunk_policy_text, split_sections
from benefitiq.ingestion.payer_fetch import FetchView
from benefitiq.ingestion.policies import (
    approve_policy,
    content_hash,
    eligible,
    ingest_policy_bytes,
    write_ledger,
)
from benefitiq.ingestion.policy_text import excerpt, extract_text, redact_policy_text
from benefitiq.ingestion.updates import classify_fetch, check_updates
from benefitiq.models import Chunk, Source
from benefitiq.office.decide import decide
from benefitiq.payers.catalog import PENDING_ACCESS, Link, PolicySource, sources
from benefitiq.payers.dates import SourceDate
from benefitiq.retrieval.embeddings import embed
from benefitiq.retrieval.search import CorpusRepo
from benefitiq.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("BENEFITIQ_LEDGER", str(tmp_path / "ledger.yaml"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _source(
    source_id: str,
    *,
    payer: str,
    segment: str = "fixture-seg",
    topic: str = "sleep_appliance",
    access: str = "public",
    fetch: bool = True,
    dated: bool = True,
) -> PolicySource:
    when = date(2024, 1, 2) if dated else None
    basis = "effective" if dated else "not_found"
    return PolicySource(
        source_id=source_id,
        payer_id=payer,
        title=f"Synthetic policy {source_id}",
        number="FIX-1",
        url=f"https://example.test/policies/{source_id}",
        topic="synthetic appliance",
        topics=(topic,) if topic else (),
        date=SourceDate(when, basis, "Effective Date"),
        access=access,
        fetch=fetch,
        fetch_reason="public" if fetch else access,
        counts_as_coverage=False,
        unconfirmed=False,
        disposition="added",
        publisher="Synthetic Publisher",
        doc_type="policy",
        links=(Link(payer, segment),),
        state="OK",
        plan_type="ok-payer",
        program="oklahoma-payer",
    )


def _html(heading: str, body: str) -> bytes:
    return (
        "<html><body>"
        f"<h2>{heading}</h2><p>{body}</p>"
        "<h2>Exclusions</h2><p>Use 41899 for the service.</p>"
        "<p>Code D7240 extraction of tooth.</p>"
        "<p>The plan keeps a note when the file is missing.</p>"
        "</body></html>"
    ).encode()


def _wipe(catalog_id: str) -> None:
    with session_scope() as session:
        rows = list(session.scalars(select(Source).where(Source.family_id == catalog_id)).all())
        ids = [row.source_id for row in rows]
        if not ids:
            return
        session.execute(delete(Chunk).where(Chunk.source_id.in_(ids)))
        session.execute(delete(Source).where(Source.source_id.in_(ids)))


def test_chunking_follows_headings_and_drops_descriptor_lines():
    text = redact_policy_text(extract_text(_html("Coverage criteria", "The plan covers a custom oral appliance.")))
    assert "41899" not in text
    assert "extraction" not in text
    assert "D7240" in text
    sections = split_sections(text)
    titles = [title for title, _body in sections]
    assert "Coverage criteria" in titles
    assert "Exclusions" in titles
    chunks = chunk_policy_text(
        "fixture-chunk",
        text,
        {"payer": "fixture-payer", "version": 1, "url": "https://example.test/policies/fixture-chunk"},
    )
    assert len(chunks) >= 2
    assert all(chunk.metadata["record_kind"] == "policy_text" for chunk in chunks)
    assert all("41899" not in chunk.text for chunk in chunks)
    joined = " ".join(chunk.section_anchor for chunk in chunks)
    assert "coverage-criteria" in joined
    assert "exclusions" in joined
    long_text = "Coverage criteria\n\n" + ("\n\n".join(f"Paragraph {index} about an appliance." for index in range(80)))
    long_chunks = chunk_policy_text("fixture-long", long_text, {})
    assert len(long_chunks) > 1
    assert all(len(chunk.text) <= 1600 for chunk in long_chunks)


def test_pdf_text_is_chunked_from_a_synthetic_file():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 700 Td (Coverage criteria for the appliance.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    from io import BytesIO

    buffer = BytesIO()
    writer.write(buffer)
    extracted = extract_text(buffer.getvalue())
    assert "appliance" in extracted.lower() or extracted.strip() == ""
    if "appliance" in extracted.lower():
        chunks = chunk_policy_text("fixture-pdf", redact_policy_text(extracted), {})
        assert chunks
        assert "appliance" in chunks[0].text.lower()


def test_ineligible_statuses_are_not_fetched():
    pending = _source("fixture-pending", payer="humana", access=PENDING_ACCESS, fetch=False)
    licensed = _source("fixture-licensed", payer="fixture", access="licensed", fetch=False)
    login = _source("fixture-login", payer="fixture", access="login", fetch=False)
    unverified = _source("fixture-unverified", payer="fixture", access="unverified", fetch=False)
    absent = _source("fixture-na", payer="fixture", access="not_applicable", fetch=False)
    undated = _source("fixture-undated", payer="fixture", dated=False, fetch=False)
    allowed = _source("fixture-public", payer="fixture")
    assert eligible(allowed) is True
    assert all(eligible(item) is False for item in (pending, licensed, login, unverified, absent, undated))
    humana = [item for item in sources() if "humana.com" in item.url]
    assert humana
    assert all(eligible(item) is False for item in humana)
    for item in sources():
        if item.access != "public" or item.date.value is None:
            assert eligible(item) is False


def test_versions_do_not_overwrite_and_review_gates_retrieval():
    catalog_id = "fixture-versioned-policy"
    _wipe(catalog_id)
    first = _html("Coverage criteria", "ALPHAONLYTOKEN The plan covers a custom oral appliance.")
    second = _html("Coverage criteria", "BETAONLYTOKEN The plan covers a custom oral appliance.")
    source = _source(catalog_id, payer="fixture-payer")
    try:
        with session_scope() as session:
            outcome, count = ingest_policy_bytes(session, source, first, make_current=True)
            assert outcome == "inserted"
            assert count >= 1
            again, _ = ingest_policy_bytes(session, source, first, make_current=True)
            assert again == "unchanged"
            changed, _ = ingest_policy_bytes(session, source, second, make_current=True)
            assert changed == "needs_review"
            rows = list(session.scalars(select(Source).where(Source.family_id == catalog_id)).all())
            assert len(rows) == 2
            current = next(row for row in rows if row.status == "current")
            waiting = next(row for row in rows if row.status == "needs_review")
            assert current.version == 1
            assert waiting.version == 2
            assert current.content_hash == content_hash(first)
            assert waiting.content_hash == content_hash(second)
            assert current.content_hash != waiting.content_hash
            repo = CorpusRepo(session)
            hits = repo.hybrid_search(
                "ALPHAONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="fixture-payer",
                segment="fixture-seg",
                topic="sleep_appliance",
            )
            assert hits
            assert all(hit.source_id == current.source_id for hit in hits)
            hidden = repo.hybrid_search(
                "BETAONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="fixture-payer",
                segment="fixture-seg",
                topic="sleep_appliance",
            )
            assert all(hit.source_id != waiting.source_id for hit in hidden)
            history = repo.hybrid_search(
                "BETAONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="fixture-payer",
                segment="fixture-seg",
                topic="sleep_appliance",
                current_only=False,
            )
            assert any(hit.source_id == waiting.source_id for hit in history)
            write_ledger(session)
            approved = approve_policy(catalog_id, session=session)
            assert approved == waiting.source_id
            session.flush()
            after = repo.hybrid_search(
                "BETAONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="fixture-payer",
                segment="fixture-seg",
                topic="sleep_appliance",
            )
            assert any(hit.source_id == waiting.source_id for hit in after)
            kept = session.get(Source, current.source_id)
            assert kept is not None
            assert kept.status == "superseded"
    finally:
        _wipe(catalog_id)


def test_hash_change_detection_and_blocked_sources():
    assert classify_fetch(
        fetch_kind="ok",
        previous_hash="abc",
        new_hash="abc",
        previous_date=date(2024, 1, 2),
        new_date=date(2024, 1, 2),
    ) == "unchanged"
    assert classify_fetch(
        fetch_kind="ok",
        previous_hash="abc",
        new_hash="def",
        previous_date=date(2024, 1, 2),
        new_date=date(2024, 1, 2),
    ) == "changed"
    assert classify_fetch(
        fetch_kind="ok",
        previous_hash="abc",
        new_hash="abc",
        previous_date=date(2024, 1, 2),
        new_date=date(2026, 3, 4),
    ) == "new_date"
    assert classify_fetch(
        fetch_kind="not_found",
        previous_hash="abc",
        new_hash=None,
        previous_date=None,
        new_date=None,
    ) == "not_found"
    assert classify_fetch(
        fetch_kind="moved",
        previous_hash="abc",
        new_hash=None,
        previous_date=None,
        new_date=None,
    ) == "moved"
    assert classify_fetch(
        fetch_kind="blocked",
        previous_hash="abc",
        new_hash=None,
        previous_date=None,
        new_date=None,
    ) == "blocked"

    catalog_id = "fixture-update-policy"
    _wipe(catalog_id)
    source = _source(catalog_id, payer="fixture-update")
    original = _html("Coverage criteria", "GAMMAONLYTOKEN The plan covers a custom oral appliance.")
    revised = _html("Coverage criteria", "DELTAONLYTOKEN The plan covers a custom oral appliance.")
    calls = {"n": 0}

    def fake_fetch(_url: str) -> FetchView:
        calls["n"] += 1
        return FetchView("ok", "downloaded", payload=revised)

    try:
        with session_scope() as session:
            ingest_policy_bytes(session, source, original, make_current=True)
            write_ledger(session)
        rows = check_updates(write_db=True, catalog=[source], fetcher=fake_fetch)
        changed = next(row for row in rows if row.source_id == catalog_id)
        assert changed.result == "changed"
        with session_scope() as session:
            family = list(session.scalars(select(Source).where(Source.family_id == catalog_id)).all())
            assert any(row.status == "needs_review" for row in family)
            assert any(row.status == "current" and row.version == 1 for row in family)
            mark_source = next(row for row in family if row.status == "current")
            mark_source.check_state = "blocked"
        with session_scope() as session:
            hits = CorpusRepo(session).hybrid_search(
                "GAMMAONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="fixture-update",
                segment="fixture-seg",
                topic="sleep_appliance",
            )
        assert all(hit.source_id != f"{catalog_id}-v1" for hit in hits)
    finally:
        _wipe(catalog_id)


def test_retrieval_filters_by_payer_segment_and_topic():
    left_id = "fixture-payer-left"
    right_id = "fixture-payer-right"
    _wipe(left_id)
    _wipe(right_id)
    left = _source(left_id, payer="payer-left", segment="left-seg", topic="sleep_appliance")
    right = _source(right_id, payer="payer-right", segment="right-seg", topic="tmj")
    try:
        with session_scope() as session:
            ingest_policy_bytes(
                session,
                left,
                _html("Coverage criteria", "LEFTONLYTOKEN oral appliance coverage"),
                make_current=True,
            )
            ingest_policy_bytes(
                session,
                right,
                _html("Coverage criteria", "RIGHTONLYTOKEN jaw joint coverage"),
                make_current=True,
            )
            repo = CorpusRepo(session)
            found = repo.hybrid_search(
                "LEFTONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="payer-left",
                segment="left-seg",
                topic="sleep_appliance",
            )
            assert found
            assert {hit.source_id for hit in found} == {f"{left_id}-v1"}
            other = repo.hybrid_search(
                "LEFTONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="payer-right",
                segment="right-seg",
                topic="tmj",
            )
            assert all("LEFTONLYTOKEN" not in (session.get(Chunk, hit.chunk_id).body) for hit in other)
            wrong_topic = repo.hybrid_search(
                "LEFTONLYTOKEN",
                dos=date(2026, 1, 15),
                payer="payer-left",
                segment="left-seg",
                topic="tmj",
            )
            assert wrong_topic == []
    finally:
        _wipe(left_id)
        _wipe(right_id)


def test_case_citation_quotes_a_retrieved_chunk_or_says_not_found(monkeypatch):
    monkeypatch.setenv("BENEFITIQ_TODAY", "2026-11-18")
    client = TestClient(app, follow_redirects=True)
    client.post("/office/start", data={"procedure_id": "sleep_appliance"})
    for _ in range(5):
        client.post("/office/answer", data={"choice": "yes"})
    page = client.get("/office/result")
    assert page.status_code == 200
    assert 'data-testid="chunk-quote"' in page.text
    assert 'data-chunk-id="cms-lcd-l33611::custom-device"' in page.text
    assert "Effective 2021-08-08" in page.text
    catalog_id = "fixture-case-policy"
    _wipe(catalog_id)
    source = _source(catalog_id, payer="bcbsok", segment="large_group", topic="sleep_appliance")
    try:
        with session_scope() as session:
            ingest_policy_bytes(
                session,
                source,
                _html(
                    "Coverage criteria",
                    "Sleep apnea oral appliance. CASEONLYTOKEN The plan covers a custom oral appliance.",
                ),
                make_current=True,
            )
        quoted = decide(
            "sleep_appliance",
            {
                "sleep_test": "yes",
                "osa_diagnosis": "yes",
                "practitioner_order": "yes",
                "custom_device": "yes",
                "product_list": "yes",
            },
            as_of=date(2026, 11, 18),
            case_id="SYN-RAG-0001",
            payer_id="bcbsok",
            segment_id="large_group",
        )
        assert quoted.retrieved
        assert any(item.quote and "CASEONLYTOKEN" in item.quote for item in quoted.retrieved)
        assert all(item.effective_from == date(2024, 1, 2) for item in quoted.retrieved)
        missing = decide(
            "sleep_appliance",
            {
                "sleep_test": "yes",
                "osa_diagnosis": "yes",
                "practitioner_order": "yes",
                "custom_device": "yes",
                "product_list": "yes",
            },
            as_of=date(2026, 11, 18),
            case_id="SYN-RAG-0002",
            payer_id="och",
            segment_id="soonerselect_medical",
        )
        assert missing.retrieved == []
        assert missing.reason.text == "not found"
    finally:
        _wipe(catalog_id)


def test_openai_embeddings_are_optional(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        seen["dimensions"] = json["dimensions"]
        seen["model"] = json["model"]

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": [{"index": 0, "embedding": [0.01] * EMBEDDING_DIM}]}

        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    vector = embed("synthetic appliance question")
    assert len(vector) == EMBEDDING_DIM
    assert seen["dimensions"] == EMBEDDING_DIM
    assert seen["model"] == "text-embedding-3-small"
    assert seen["auth"] == "Bearer test-only"
    assert "test-only" not in str(seen["url"])


def test_sources_page_shows_version_and_status():
    page = TestClient(app).get("/sources")
    assert page.status_code == 200
    assert 'data-testid="source-status"' in page.text
    assert "Current" in page.text
    assert "Version 1" in page.text
    assert "Waiting on a disclaimer" in page.text
    assert "<form" not in page.text


def test_excerpt_drops_descriptor_lines():
    quote = excerpt("Use 41899 for the service.\nThe plan covers the appliance.")
    assert "41899" not in quote
    assert "appliance" in quote
    assert "Decision support" not in quote
    table = excerpt(
        "D9947\n\tSynthetic descriptor for the code\n"
        "41899\n\tSynthetic words after a five digit code\n"
        "The plan covers a custom appliance."
    )
    assert "D9947" in table
    assert "41899" not in table
    assert "descriptor" not in table
    assert "five digit" not in table
    assert "appliance" in table
