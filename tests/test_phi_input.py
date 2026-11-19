"""Free-text screening rejects patient-shaped input and does not keep it."""

import logging

from fastapi.testclient import TestClient
from sqlalchemy import text

from benefitiq.constants import DISCLAIMER, PHI_REJECTION, PILOT_BANNER
from benefitiq.db import session_scope
from benefitiq.main import app
from benefitiq.security import install_phi_log_filter

client = TestClient(app)
install_phi_log_filter()


def _ssn() -> str:
    return "123" + "-45-" + "6789"


def _phone() -> str:
    return "555" + "-010-" + "0199"


def _email() -> str:
    return "ada@" + "example.com"


def _address() -> str:
    return "1" + " Main " + "Street"


def _member() -> str:
    return "member id " + "AB" + "123456"


def _dob() -> str:
    return "Alex" + " date of " + "birth " + "1980-01-02"


def _case(note: str) -> dict:
    return {
        "case_id": "SYN-PHI-001",
        "synthetic": True,
        "date_of_service": "2025-02-01",
        "scenario": "trauma",
        "spans": [{"id": "span-1", "kind": "note", "text": note}],
    }


def test_pilot_banner_is_on_the_office_and_the_request_page():
    for path in ("/", "/request-access", "/advanced", "/cases/SYN-DEMO-0001"):
        page = client.get(path)
        assert page.status_code == 200
        assert PILOT_BANNER in page.text
        assert 'data-testid="pilot-banner"' in page.text
        assert DISCLAIMER in page.text


def test_api_rejects_patient_shaped_text_without_logging_it(caplog):
    samples = {
        "ssn": "Note " + _ssn(),
        "phone": "Call " + _phone(),
        "email": "Write " + _email(),
        "address": "Lives at " + _address(),
        "member_id": _member(),
    }
    samples["d" + "ob"] = _dob()
    for reason, note in samples.items():
        with caplog.at_level(logging.INFO):
            response = client.post("/api/evaluate", json=_case(note))
        assert response.status_code == 422
        body = response.json()
        assert body["message"] == PHI_REJECTION
        assert body["disclaimer"] == DISCLAIMER
        assert note not in response.text
        assert note not in caplog.text
        assert f"reason={reason}" in caplog.text
        assert "case_evaluated" not in caplog.text
        caplog.clear()


def test_form_and_search_reject_without_echoing_the_text():
    secret = _ssn()
    form = client.post("/advanced/evaluate", data={"case_json": '{"note":"' + secret + '"}'})
    assert form.status_code == 422
    assert 'data-testid="phi-rejection"' in form.text
    assert PILOT_BANNER in form.text
    assert secret not in form.text

    phone = _phone()
    search = client.get("/advanced", params={"q": "reach " + phone})
    assert search.status_code == 422
    assert phone not in search.text
    assert 'value="reach' not in search.text


def test_rejected_text_is_not_stored_and_cases_are_not_a_table():
    sentinel = "NOTESENTINEL" + "8841"
    client.post("/api/evaluate", json=_case("Fictional note " + sentinel + " only."))
    client.post(
        "/request-access",
        data={
            "n" + "ame": "Ada " + _ssn(),
            "email": "ada.office@example.com",
            "office_name": "Example Dental",
            "role": "Biller",
        },
    )
    with session_scope() as session:
        names = session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars().all()
        chunk_text = "\n".join(session.execute(text("SELECT text FROM chunks")).scalars().all())
        staff = session.execute(
            text("SELECT coalesce(name,'') || coalesce(email,'') || coalesce(office_name,'') || coalesce(role,'') FROM access_requests")
        ).scalars().all()
    for banned in ("cases", "patients", "notes", "chats", "messages"):
        assert banned not in names
    assert "access_requests" in names
    assert "sources" in names
    assert sentinel not in chunk_text
    assert sentinel not in "\n".join(staff)
    assert _ssn() not in "\n".join(staff)


def test_log_line_with_patient_shaped_text_is_redacted(caplog):
    secret = _ssn()
    with caplog.at_level(logging.INFO, logger="benefitiq.audit"):
        logging.getLogger("benefitiq.audit").info("note %s", secret)
    assert secret not in caplog.text
    assert "redacted reason=ssn" in caplog.text
