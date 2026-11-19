"""Reject free text that looks like patient information.

The submitted text is not logged. Reason codes are the only record.
Dates of service without a birth-date hint are left alone.
"""

from __future__ import annotations

import re

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SSN_SPACE_RE = re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
MEMBER_RE = re.compile(
    r"(?i)\b(?:member|subscriber|beneficiary|insurance|policy|mbi|hicn)[\s_]*(?:id|number|no|#)\b"
    r"\s*[:=#]?\s*(?!SYN-)(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{4,}"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
ADDRESS_RE = re.compile(
    r"(?i)\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|"
    r"way|place|pl|terrace|circle|cir)\b"
)
PO_BOX_RE = re.compile(r"(?i)\bP\.?\s*O\.?\s*Box\s+\d+\b")
DOB_HINT_RE = re.compile(r"(?i)\b(?:date of birth|birth date|birthdate|dob|born on|birthday)\b")
DATE_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
JSON_PATIENT_RE = re.compile(
    r'(?i)"(?:dob|date_of_birth|birth_date|patient_name|full_name|ssn|social_security|'
    r'member_id|subscriber_id|phone|address|street)"\s*:'
)
WORK_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def phi_reason(text: str, *, allow_email: bool = False) -> str | None:
    """Return a short reason code, or None when the text is safe to handle."""
    if not text:
        return None
    if SSN_RE.search(text) or SSN_SPACE_RE.search(text):
        return "ssn"
    if PHONE_RE.search(text):
        return "phone"
    if MEMBER_RE.search(text):
        return "member_id"
    if ADDRESS_RE.search(text) or PO_BOX_RE.search(text):
        return "address"
    if JSON_PATIENT_RE.search(text):
        return "patient_field"
    if _has_dob(text):
        return "dob"
    if not allow_email and EMAIL_RE.search(text):
        return "email"
    return None


def _has_dob(text: str) -> bool:
    for match in DOB_HINT_RE.finditer(text):
        start = max(0, match.start() - 48)
        end = min(len(text), match.end() + 48)
        if DATE_RE.search(text[start:end]):
            return True
    return False


def valid_work_email(value: str) -> bool:
    return WORK_EMAIL_RE.fullmatch(value.strip()) is not None
