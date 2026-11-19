"""Shared constants. The disclaimer text is part of the product contract."""

DISCLAIMER = (
    "Decision support for a human biller. Not a final coding or coverage determination."
)

# Width of the pgvector column. Every embedding provider uses this width.
# Changing it means re-ingesting the corpus.
EMBEDDING_DIM = 384

# Internal safety ranking for exam-family code numbers.
# Labels live in labels.yaml. These ranks are not code descriptors.
EXAM_RANK = {
    "D0140": 1,
    "D0150": 2,
    "D0160": 3,
}

EXAM_REQUIRED = {
    "D0140": ["limited_exam", "specific_complaint"],
    "D0150": [
        "limited_exam",
        "specific_complaint",
        "full_dentition_review",
        "periodontal_status",
        "medical_history_review",
        "treatment_plan",
    ],
    "D0160": [
        "limited_exam",
        "specific_complaint",
        "full_dentition_review",
        "periodontal_status",
        "medical_history_review",
        "treatment_plan",
        "extensive_problem_review",
    ],
}

CODE_SYSTEMS = {"ICD10CM", "CDT", "HCPCS", "CPT"}

FORBIDDEN_CONTENT_KEYS = {
    "descriptor",
    "description",
    "official_description",
    "long_description",
    "cdt_descriptor",
    "cpt_descriptor",
    "lay_description",
    "consumer_descriptor",
}
