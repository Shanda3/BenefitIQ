"""Fail the build when fixtures or code resemble real patient identifiers.

The scanner looks for SSN-shaped numbers, phone numbers, member-id assignments,
and a personal name sitting next to a date of birth. Synthetic case ids and
dates of service are allowed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
TEXT_SUFFIXES = {
    ".py",
    ".yml",
    ".yaml",
    ".md",
    ".json",
    ".html",
    ".css",
    ".txt",
    ".toml",
    ".sql",
    ".example",
}
EXTRA_NAMES = {"Dockerfile", "docker-compose.yml", ".gitignore"}

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SSN_SPACE_RE = re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
MEMBER_RE = re.compile(
    r"(?i)\b(?:member|subscriber|beneficiary|insurance|policy|mbi|hicn)[\s_]*(?:id|number|no|#)\b"
    r"\s*[:=]\s*(?!SYN-)[A-Za-z0-9][A-Za-z0-9-]{5,}"
)
DOB_HINT_RE = re.compile(r"(?i)\b(?:date of birth|birth date|dob|born on|birthday)\b")
NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b")
DATE_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
JSON_DOB_RE = re.compile(r'(?i)"(?:dob|date_of_birth|birth_date)"\s*:')
JSON_NAME_RE = re.compile(
    r'(?i)"(?:name|patient_name|full_name|first_name|last_name)"\s*:\s*"(?!SYN-)'
)


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in EXTRA_NAMES:
            yield path


def scan_text(text: str, label: str) -> list[str]:
    findings: list[str] = []
    for name, pattern in (
        ("ssn", SSN_RE),
        ("ssn", SSN_SPACE_RE),
        ("phone", PHONE_RE),
        ("member_id", MEMBER_RE),
    ):
        for match in pattern.finditer(text):
            findings.append(f"{label}: {name} pattern near offset {match.start()}")

    for match in DOB_HINT_RE.finditer(text):
        start = max(0, match.start() - 160)
        end = min(len(text), match.end() + 160)
        window = text[start:end]
        if NAME_RE.search(window) and DATE_RE.search(window):
            findings.append(f"{label}: name and date of birth appear together")
            break

    if JSON_DOB_RE.search(text) and JSON_NAME_RE.search(text):
        findings.append(f"{label}: structured name and date-of-birth fields")
    return findings


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(text, str(path)))
    return findings


def scan_repo(root: Path = ROOT) -> list[str]:
    return scan_paths(list(iter_files(root)))


def main() -> int:
    findings = scan_repo()
    if findings:
        print("PHI-like patterns found:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("phi scan: no PHI-like patterns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
