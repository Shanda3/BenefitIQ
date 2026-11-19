"""Shared constants. The disclaimer text is part of the product contract."""

DISCLAIMER = (
    "Decision support for a human biller. Not a final coding or coverage determination."
)

# Shown on every page of the private pilot. It is not a substitute for the disclaimer.
PILOT_BANNER = (
    "Pilot: do not enter patient names, dates of birth, member IDs, or other patient details. "
    "Decision support for a human biller."
)

# Returned when free text looks like patient information. The submitted text is not included.
PHI_REJECTION = (
    "That text looks like patient information. "
    "Remove names, dates of birth, member IDs, and other patient details, then try again."
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
