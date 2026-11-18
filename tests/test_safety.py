import json
import logging

from benefitiq.constants import DISCLAIMER
from benefitiq.engine.draft import Draft, DraftLine
from benefitiq.engine.recommend import evaluate
from benefitiq.labels import load_labels
from scripts.phi_scan import scan_repo, scan_text
from tests.conftest import ROOT, load_case


def _phrase(*parts: str) -> str:
    return "".join(parts)


# Stored in pieces so the denylist itself does not contain a descriptor string.
BANNED_DESCRIPTOR_FRAGMENTS = (
    _phrase("periodic oral ", "evaluation"),
    _phrase("comprehensive oral ", "evaluation"),
    _phrase("oral evaluation", ", problem"),
    _phrase("cone beam", " ct"),
    _phrase("core buildup, ", "including any pins"),
)


def test_labels_are_placeholders_only():
    labels = load_labels()
    assert labels
    for code, label in labels.items():
        assert label.startswith("placeholder:")
        lowered = label.lower()
        for fragment in BANNED_DESCRIPTOR_FRAGMENTS:
            assert fragment not in lowered
        assert code not in ("descriptor", "description")


def test_repository_has_no_copyrighted_descriptor_fragments():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".yml", ".yaml", ".json", ".html", ".css", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for fragment in BANNED_DESCRIPTOR_FRAGMENTS:
            if fragment in text:
                offenders.append(f"{path}: {fragment}")
    assert offenders == []


def test_phi_scan_accepts_repo_and_flags_samples(tmp_path):
    assert scan_repo(ROOT) == []

    ssn = "123" + "-45-" + "6789"
    phone = "555" + "-010-" + "0199"
    name = "Alex" + " " + "Morgan"
    dob_line = name + " date of " + "birth " + "1980-01-02"
    member_line = "member_id=" + "AB" + "1234567"

    assert scan_text(ssn, "mem") 
    assert scan_text(phone, "mem")
    assert scan_text(dob_line, "mem")
    assert scan_text(member_line, "mem")

    bad = tmp_path / "case.json"
    bad.write_text(json.dumps({"note": ssn}), encoding="utf-8")
    from scripts.phi_scan import scan_paths

    assert scan_paths([bad])


def test_audit_log_has_case_id_and_not_the_note(caplog):
    sentinel = "UNIQUECLINICALSENTINEL"
    case = load_case("trauma.json")
    span = case.spans[0].model_copy(update={"text": case.spans[0].text + " " + sentinel})
    case = case.model_copy(update={"spans": [span, *case.spans[1:]]})
    with caplog.at_level(logging.INFO, logger="benefitiq.audit"):
        evaluate(case)
    assert "case_evaluated case_id=SYN-TRAUMA-001" in caplog.text
    assert sentinel not in caplog.text


def test_validator_replaces_forged_citation_with_not_found():
    case = load_case("trauma.json")

    class ForgedDrafter:
        def draft(self, case, repo, evidence):
            return Draft(
                lines=[
                    DraftLine(
                        pathway="medical",
                        kind="coding",
                        code_system="ICD10CM",
                        code="S02.5XXA",
                        supporting_span_ids=["span-er", "span-dental"],
                        source_id="not-a-real-source",
                        chunk_id="not-a-real-chunk",
                    )
                ]
            )

    result = evaluate(case, drafter=ForgedDrafter())
    line = next(item for item in result.medical_lines if item.code == "S02.5XXA")
    assert line.status == "not_found"
    assert line.text == "not found"
    assert line.citations == []
    assert result.date_of_service == case.date_of_service
    assert result.date_of_service_altered is False
    assert result.disclaimer == DISCLAIMER
    assert "https://evil.example" not in result.model_dump_json()


def test_hard_guards_still_apply_when_drafter_is_replaced():
    case = load_case("upcoding.json")

    class EagerDrafter:
        def draft(self, case, repo, evidence):
            from benefitiq.retrieval.search import CorpusRepo

            assert isinstance(repo, CorpusRepo)
            rules = repo.records(
                "coding_rule",
                scenario=case.scenario,
                dos=case.date_of_service,
                state=case.state,
                plan_type=case.plan_type,
                payers=[case.payer_dental],
                payer_mode="match_or_general",
            )
            higher = next(rule for rule in rules if rule.metadata.get("code") == "D0150")
            return Draft(
                lines=[
                    DraftLine(
                        pathway="dental",
                        kind="coding",
                        code_system="CDT",
                        code="D0150",
                        supporting_span_ids=["span-exam"],
                        source_id=higher.source_id,
                        chunk_id=higher.chunk_id,
                    ),
                    DraftLine(
                        pathway="medical",
                        kind="coding",
                        code_system="ICD10CM",
                        code="C06.9",
                        supporting_span_ids=["span-exam"],
                        source_id=higher.source_id,
                        chunk_id=higher.chunk_id,
                    ),
                ]
            )

    result = evaluate(case, drafter=EagerDrafter())
    cited = [line.code for line in result.dental_lines + result.medical_lines if line.status == "cited"]
    assert "D0150" not in cited
    assert "C06.9" not in cited
    assert any(guard.type == "upcoding" and "D0150" in guard.codes for guard in result.guardrails)
    assert any(guard.type == "malignancy" and "C06.9" in guard.codes for guard in result.guardrails)
    assert result.date_of_service_altered is False
