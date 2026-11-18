"""Read documentation spans. Tokens are internal element names, not code descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field

from benefitiq.schemas import Span

# A span supports an element only when its text contains one of these tokens.
ELEMENT_TOKENS: dict[str, tuple[str, ...]] = {
    "tooth_fracture": ("tooth fracture", "tooth fractured", "fractured upper"),
    "er_note": ("emergency department",),
    "dental_exam": ("dental exam",),
    "bicycle_fall": ("bicycle", "bike"),
    "date_of_injury": ("date of injury",),
    "place_of_injury": ("place of injury",),
    "oral_lesion": ("oral lesion",),
    "biopsy_performed": ("biops",),
    "pathology_report": ("final pathology", "pathology report confirms"),
    "pathology_confirms_malignancy": ("pathology confirms malignancy",),
    "osa_diagnosis": ("sleep apnea",),
    "sleep_study": ("sleep study",),
    "prescription": ("prescription",),
    "cpap_intolerance": ("cpap intoleran",),
    "cbct_captured": ("cbct",),
    "implant_planning": ("implant planning",),
    "limited_exam": ("limited exam",),
    "specific_complaint": ("complaint",),
    "full_dentition_review": ("full dentition",),
    "periodontal_status": ("periodontal status",),
    "medical_history_review": ("medical history",),
    "treatment_plan": ("treatment plan",),
    "extensive_problem_review": ("extensive problem review",),
    "prophylaxis_done": ("prophylaxis",),
    "crown_procedure": ("crown procedure",),
    "core_procedure": ("core material",),
}


@dataclass
class Evidence:
    elements: set[str] = field(default_factory=set)
    span_ids: dict[str, list[str]] = field(default_factory=dict)

    def spans_for(self, elements: list[str]) -> list[str]:
        found: list[str] = []
        for element in elements:
            for span_id in self.span_ids.get(element, []):
                if span_id not in found:
                    found.append(span_id)
        return found


def extract_evidence(spans: list[Span]) -> Evidence:
    evidence = Evidence()
    for span in spans:
        lowered = span.text.lower()
        for element, tokens in ELEMENT_TOKENS.items():
            if any(token in lowered for token in tokens):
                evidence.elements.add(element)
                evidence.span_ids.setdefault(element, [])
                if span.id not in evidence.span_ids[element]:
                    evidence.span_ids[element].append(span.id)
    return evidence
