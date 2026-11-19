from datetime import date

from benefitiq.constants import EMBEDDING_DIM
from benefitiq.db import session_scope
from benefitiq.retrieval.embeddings import embed
from benefitiq.retrieval.search import CorpusRepo


def test_embeddings_are_deterministic_and_local(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    vector = embed("tooth fracture")
    assert len(vector) == EMBEDDING_DIM
    assert vector == embed("tooth fracture")
    assert vector != embed("unrelated token")


def test_exact_code_lookup_returns_source_window():
    with session_scope() as session:
        hits = CorpusRepo(session).lookup_code("D0367", dos=date(2025, 6, 2), state="XX", plan_type="commercial")
    assert hits
    assert hits[0].code == "D0367"
    assert hits[0].url == "https://example.test/stub/code-index"
    assert hits[0].effective_from <= date(2025, 6, 2)
    assert hits[0].chunk_id


def test_hybrid_search_respects_effective_window_and_plan():
    with session_scope() as session:
        repo = CorpusRepo(session)
        early = repo.hybrid_search(
            "FREQOLDTOKEN",
            dos=date(2025, 3, 15),
            payer="Stub Payer A",
            state="XX",
            plan_type="commercial",
        )
        before = repo.hybrid_search(
            "FREQOLDTOKEN",
            dos=date(2023, 1, 1),
            payer="Stub Payer A",
            plan_type="commercial",
        )
        later = repo.hybrid_search(
            "FREQNEWTOKEN",
            dos=date(2025, 8, 1),
            payer="Stub Payer A",
            plan_type="commercial",
        )
        wrong_plan = repo.hybrid_search(
            "MEDICAIDONLYTOKEN",
            dos=date(2025, 3, 1),
            state="XX",
            plan_type="commercial",
        )
        medicaid = repo.hybrid_search(
            "MEDICAIDONLYTOKEN",
            dos=date(2025, 3, 1),
            payer="Stub Medicaid Plan",
            state="ZZ",
            plan_type="medicaid",
        )
        trauma = repo.hybrid_search(
            "traumatic tooth fracture bicycle",
            dos=date(2025, 3, 12),
            state="XX",
            plan_type="commercial",
        )

    assert any(hit.url.endswith("v2024") for hit in early)
    assert all("v2025" not in hit.url for hit in early)
    assert all(hit.effective_from <= date(2025, 3, 15) for hit in early)
    assert all(hit.effective_to is None or hit.effective_to >= date(2025, 3, 15) for hit in early)
    assert all(not hit.url.endswith("v2024") for hit in before)
    assert any(hit.url.endswith("v2025") for hit in later)
    assert all("medicaid" not in hit.url for hit in wrong_plan)
    matched = [hit for hit in medicaid if hit.url.endswith("medicaid-dental")]
    assert matched
    assert matched[0].chunk_id
    assert matched[0].effective_from == date(2024, 1, 1)
    assert any("trauma" in hit.url for hit in trauma)
    assert any(hit.vector_score > 0 or hit.keyword_score > 0 for hit in trauma)
    assert all(hit.chunk_id and hit.url and hit.effective_from for hit in trauma)
