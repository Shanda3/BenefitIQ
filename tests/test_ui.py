from fastapi.testclient import TestClient

from benefitiq.constants import DISCLAIMER
from benefitiq.main import app

client = TestClient(app)


def test_pages_and_api_carry_the_disclaimer():
    home = client.get("/")
    assert home.status_code == 200
    assert DISCLAIMER in home.text

    page = client.post("/advanced/evaluate", data={"sample_id": "trauma"})
    assert page.status_code == 200
    assert DISCLAIMER in page.text
    assert 'data-testid="decision-support-banner"' in page.text
    assert "Dental pathway" in page.text
    assert "Medical pathway" in page.text
    assert "S02.5XXA" in page.text
    assert "https://example.test/stub/trauma-rules" in page.text
    assert "2024-01-01" in page.text

    sample = client.get("/advanced/sample/biopsy")
    assert sample.status_code == 200
    assert DISCLAIMER in sample.text
    assert "K13.70" in sample.text
    assert "pathology_report" in sample.text

    payload = {
        "case_id": "SYN-API-001",
        "synthetic": True,
        "date_of_service": "2025-02-01",
        "payer_dental": "Synthetic Payer Not In Corpus",
        "state": "XX",
        "plan_type": "commercial",
        "scenario": "unknown_coverage",
        "requested_codes": [{"system": "CDT", "code": "D1110"}],
        "spans": [{"id": "span-1", "kind": "note", "text": "Fictional adult patient. Prophylaxis completed."}],
    }
    api = client.post("/api/evaluate", json=payload)
    assert api.status_code == 200
    body = api.json()
    assert body["disclaimer"] == DISCLAIMER
    assert body["date_of_service_altered"] is False
    assert all(line["status"] == "not_found" for line in body["dental_lines"])


def test_api_rejects_non_synthetic_without_echoing_the_note():
    response = client.post(
        "/api/evaluate",
        json={
            "case_id": "SYN-BAD-001",
            "synthetic": False,
            "date_of_service": "2025-03-12",
            "scenario": "trauma",
            "spans": [
                {
                    "id": "span-1",
                    "kind": "note",
                    "text": "Fictional sentence that must stay out of the error payload.",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["disclaimer"] == DISCLAIMER
    assert "must stay out of the error payload" not in response.text


def test_health_reports_loaded_sources():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["sources"] > 0
