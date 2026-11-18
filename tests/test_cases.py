"""Eight synthetic cases. Expectations are behaviors against the stub corpus."""

from datetime import date

from benefitiq.constants import DISCLAIMER
from tests.conftest import evaluate_sample


def cited_codes(result):
    codes = []
    for line in [*result.dental_lines, *result.medical_lines]:
        if line.status == "cited" and line.code:
            codes.append(line.code)
    return codes


def find_line(result, code, kind=None):
    for line in [*result.dental_lines, *result.medical_lines]:
        if line.code == code and (kind is None or line.kind == kind):
            return line
    raise AssertionError(f"{code} ({kind}) not on the result")


def gap_elements(result):
    return {gap.element for gap in result.documentation_missing}


def assert_citation_covers(citation, dos):
    assert citation.url.startswith("https://")
    assert citation.effective_from <= dos
    assert citation.effective_to is None or citation.effective_to >= dos
    assert citation.chunk_id
    assert citation.source_id


def test_trauma_medical_pathway_and_injury_details():
    result = evaluate_sample("trauma.json")
    assert result.disclaimer == DISCLAIMER
    assert result.date_of_service_altered is False
    assert result.date_of_service == date(2025, 3, 12)
    assert result.routing.status == "cited"
    assert result.routing.pathway == "medical"
    assert "medical" in result.routing.text
    assert result.routing.citations
    assert_citation_covers(result.routing.citations[0], result.date_of_service)

    for code in ("S02.5XXA", "V18.0XXA"):
        line = find_line(result, code)
        assert line.status == "cited"
        assert line.pathway == "medical"
        assert line.supporting_span_ids
        assert line.label.startswith("placeholder:")
        assert_citation_covers(line.citations[0], result.date_of_service)

    missing = evaluate_sample("trauma_missing_details.json")
    assert missing.routing.status == "cited"
    assert missing.routing.pathway == "medical"
    assert "S02.5XXA" in cited_codes(missing)
    assert "V18.0XXA" not in cited_codes(missing)
    assert {"date_of_injury", "place_of_injury"} <= gap_elements(missing)
    assert all(gap.status == "documentation missing" for gap in missing.documentation_missing)


def test_biopsy_lesion_code_not_malignancy():
    result = evaluate_sample("biopsy.json")
    assert result.disclaimer == DISCLAIMER
    lesion = find_line(result, "K13.70")
    assert lesion.status == "cited"
    assert lesion.label.startswith("placeholder:")
    assert "malign" not in lesion.label
    assert lesion.supporting_span_ids
    assert_citation_covers(lesion.citations[0], result.date_of_service)
    assert "C06.9" not in cited_codes(result)
    assert any(guard.type == "malignancy" and "C06.9" in guard.codes for guard in result.guardrails)
    assert "pathology_report" in gap_elements(result)


def test_sleep_apnea_appliance_requirements():
    result = evaluate_sample("osa.json")
    assert result.disclaimer == DISCLAIMER
    assert result.routing.status == "cited"
    assert result.routing.pathway == "medical"
    assert "equipment" in result.routing.text
    assert_citation_covers(result.routing.citations[0], result.date_of_service)

    requirements = {
        line.requirement_element: line
        for line in result.medical_lines
        if line.kind == "requirement"
    }
    assert requirements["sleep_study"].requirement_status == "met"
    assert requirements["prescription"].requirement_status == "met"
    assert requirements["cpap_intolerance"].requirement_status == "missing"
    for line in requirements.values():
        assert line.status == "cited"
        assert_citation_covers(line.citations[0], result.date_of_service)
        assert "osa" in line.citations[0].url
    assert "cpap_intolerance" in gap_elements(result)
    assert "E0486" not in cited_codes(result)


def test_cbct_coverage_not_found_without_policy():
    result = evaluate_sample("cbct.json")
    assert result.disclaimer == DISCLAIMER
    lines = [line for line in result.dental_lines if line.code == "D0367"]
    assert lines
    assert all(line.status == "not_found" for line in lines)
    assert all(line.citations == [] for line in lines)
    assert all(line.frequency_token is None for line in lines)
    assert all(line.text == "not found" for line in lines)
    assert not any(line.status == "cited" for line in [result.routing, *result.dental_lines, *result.medical_lines])
    blob = result.model_dump_json()
    assert "FREQ" not in blob
    assert "not found" in blob


def test_upcoding_refuses_higher_exam_and_date_change():
    result = evaluate_sample("upcoding.json")
    assert result.disclaimer == DISCLAIMER
    assert result.date_of_service == date(2025, 4, 2)
    assert result.date_of_service_altered is False
    assert any(
        guard.type == "upcoding" and guard.status == "refused" and "D0150" in guard.codes
        for guard in result.guardrails
    )
    assert any(guard.type == "date_of_service" and guard.status == "refused" for guard in result.guardrails)
    assert {
        "full_dentition_review",
        "periodontal_status",
        "medical_history_review",
        "treatment_plan",
    } <= gap_elements(result)
    assert "D0150" not in cited_codes(result)
    supported = find_line(result, "D0140")
    assert supported.status == "cited"
    assert supported.supporting_span_ids
    assert "2025-01-01" not in result.model_dump_json()


def test_unbundling_flag_does_not_split_components():
    result = evaluate_sample("unbundling.json")
    assert result.disclaimer == DISCLAIMER
    flags = [guard for guard in result.guardrails if guard.type == "unbundling"]
    assert flags
    flag = flags[0]
    assert flag.status == "flagged"
    assert {"D2740", "D2950"} <= set(flag.codes)
    assert flag.citations
    assert "bundling" in flag.citations[0].url
    assert_citation_covers(flag.citations[0], result.date_of_service)
    assert "D2740" not in cited_codes(result)
    assert "D2950" not in cited_codes(result)
    blob = result.model_dump_json().lower()
    assert "unbundle them" not in blob
    assert "bill separately" not in blob


def test_effective_date_uses_version_in_window():
    early = evaluate_sample("effective_date.json")
    line = find_line(early, "D1110", kind="coverage")
    assert line.status == "cited"
    assert line.frequency_token == "FREQ-OLD"
    assert line.supporting_span_ids
    assert any(citation.url.endswith("v2024") for citation in line.citations)
    assert all("v2025" not in citation.url for citation in line.citations)
    assert line.citations[0].effective_from == date(2024, 1, 1)
    assert line.citations[0].effective_to == date(2025, 6, 30)
    assert "FREQ-NEW" not in early.model_dump_json()

    later = evaluate_sample("effective_date.json", date_of_service=date(2025, 8, 1))
    new_line = find_line(later, "D1110", kind="coverage")
    assert new_line.status == "cited"
    assert new_line.frequency_token == "FREQ-NEW"
    assert any("v2025" in citation.url for citation in new_line.citations)
    assert all(not citation.url.endswith("v2024") for citation in new_line.citations)
    assert new_line.citations[0].effective_from == date(2025, 7, 1)
    assert "FREQ-OLD" not in later.model_dump_json()


def test_no_coverage_source_is_not_found_everywhere():
    result = evaluate_sample("no_coverage.json")
    assert result.disclaimer == DISCLAIMER
    lines = [result.routing, *result.dental_lines, *result.medical_lines]
    assert result.dental_lines
    assert result.medical_lines
    assert all(line.status == "not_found" for line in lines)
    assert all(line.citations == [] for line in lines)
    assert all(line.frequency_token is None for line in lines)
    assert all(line.text == "not found" for line in lines)
    assert not any(guard.citations for guard in result.guardrails)
    assert "not found" in result.model_dump_json()
