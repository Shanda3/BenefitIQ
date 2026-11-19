"""Load the office question trees. New procedures are content, not code."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

from benefitiq.config import questions_path
from benefitiq.constants import FORBIDDEN_CONTENT_KEYS
from benefitiq.labels import load_labels

RESULT_IDS = ("bill_medical_first", "probably_not_medical", "need_more_info")
BENEFIT_PATHS = ("medical", "dental")
RESULT_LABELS = {
    "bill_medical_first": "Bill medical first",
    "probably_not_medical": "Probably not medical",
    "need_more_info": "Need more info",
}
ANSWERS = {"yes", "no", "not_sure"}
CPT_RE = re.compile(r"^[0-9]{4}[0-9A-Z]$")
BANNED_PHRASE = "maximize " + "reimbursement"


class TreeError(ValueError):
    """The question tree cannot be used as written."""


def _reject_forbidden(value: Any, *, where: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_CONTENT_KEYS:
                raise TreeError(f"{where} contains forbidden field '{key}'")
            _reject_forbidden(child, where=where)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden(child, where=where)


def _citations(value: Any, *, where: str, allow_empty: bool) -> list[dict[str, str]]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise TreeError(f"{where} citations must be a list")
    cleaned: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise TreeError(f"{where} citation {index} must be a mapping")
        source_id = item.get("source_id")
        section = item.get("section")
        if not isinstance(source_id, str) or not source_id.strip():
            raise TreeError(f"{where} citation {index} needs a source_id")
        if not isinstance(section, str) or not section.strip():
            raise TreeError(f"{where} citation {index} needs a section")
        cleaned.append({"source_id": source_id.strip(), "section": section.strip()})
    if not cleaned and not allow_empty:
        raise TreeError(f"{where} needs a citation")
    return cleaned


def _clauses(value: Any, *, where: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise TreeError(f"{where} must be a non-empty list")
    cleaned: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise TreeError(f"{where}[{index}] must be a mapping")
        question_id = item.get("id")
        answer = item.get("answer")
        if not isinstance(question_id, str) or not question_id.strip():
            raise TreeError(f"{where}[{index}] needs an id")
        if not isinstance(answer, str) or answer not in ANSWERS:
            raise TreeError(f"{where}[{index}] answer must be yes, no, or not_sure")
        cleaned.append({"id": question_id.strip(), "answer": answer})
    return cleaned


def _codes(value: Any, *, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TreeError(f"{where} codes must be a list")
    labels = load_labels()
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        spot = f"{where} code {index}"
        if not isinstance(item, dict):
            raise TreeError(f"{spot} must be a mapping")
        if "label" in item:
            raise TreeError(f"{spot} must not carry a label. Labels come from labels.yaml.")
        status = item.get("status")
        if status not in {"consider", "not_recommended", "not_found"}:
            raise TreeError(f"{spot} has an unknown status")
        note = item.get("note")
        if not isinstance(note, str) or not note.strip():
            raise TreeError(f"{spot} needs a note")
        code = item.get("code")
        system = item.get("system")
        if status == "not_found" and not code:
            cleaned.append(
                {
                    "system": None,
                    "code": None,
                    "status": "not_found",
                    "note": note.strip(),
                    "citations": _citations(item.get("citations"), where=spot, allow_empty=True),
                }
            )
            continue
        if system == "CPT" or not isinstance(system, str):
            raise TreeError(f"{spot} needs a non-CPT code system")
        if not isinstance(code, str) or not code.strip():
            raise TreeError(f"{spot} needs a code number")
        code = code.strip()
        if CPT_RE.fullmatch(code):
            raise TreeError(f"{spot} looks like a CPT code and is not allowed")
        if code not in labels:
            raise TreeError(f"{spot} code {code} is missing from labels.yaml")
        cleaned.append(
            {
                "system": system.strip(),
                "code": code,
                "status": status,
                "note": note.strip(),
                "citations": _citations(item.get("citations"), where=spot, allow_empty=False),
            }
        )
    return cleaned


def _checklist(value: Any, *, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TreeError(f"{where} checklist must be a list")
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        spot = f"{where} checklist {index}"
        if not isinstance(item, dict):
            raise TreeError(f"{spot} must be a mapping")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TreeError(f"{spot} needs text")
        uncited = bool(item.get("uncited"))
        citations = _citations(item.get("citations"), where=spot, allow_empty=uncited)
        if uncited and citations:
            raise TreeError(f"{spot} is marked uncited and also has citations")
        if not uncited and not citations:
            raise TreeError(f"{spot} needs a citation or uncited: true")
        cleaned.append({"text": text.strip(), "citations": citations, "uncited": uncited})
    return cleaned


def _questions(value: Any, *, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 3 <= len(value) <= 5:
        raise TreeError(f"{where} needs 3 to 5 questions")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        spot = f"{where} question {index}"
        if not isinstance(item, dict):
            raise TreeError(f"{spot} must be a mapping")
        question_id = item.get("id")
        prompt = item.get("prompt")
        gap = item.get("gap")
        if not isinstance(question_id, str) or not question_id.strip():
            raise TreeError(f"{spot} needs an id")
        question_id = question_id.strip()
        if question_id in seen:
            raise TreeError(f"{where} repeats question {question_id}")
        seen.add(question_id)
        if not isinstance(prompt, str) or not prompt.strip():
            raise TreeError(f"{spot} needs a prompt")
        if not isinstance(gap, str) or not gap.strip():
            raise TreeError(f"{spot} needs a gap label")
        gap_on = item.get("gap_on") or []
        if not isinstance(gap_on, list) or not all(isinstance(token, str) and token in ANSWERS for token in gap_on):
            raise TreeError(f"{spot} gap_on must be yes, no, or not_sure")
        help_text = item.get("help") or ""
        if not isinstance(help_text, str):
            raise TreeError(f"{spot} help must be text")
        cleaned.append(
            {
                "id": question_id,
                "prompt": prompt.strip(),
                "help": help_text.strip(),
                "gap": gap.strip(),
                "gap_on": list(gap_on),
                "citations": _citations(item.get("citations"), where=spot, allow_empty=False),
            }
        )
    return cleaned


def _procedure(raw: Any, *, index: int) -> dict[str, Any]:
    where = f"procedure {index}"
    if not isinstance(raw, dict):
        raise TreeError(f"{where} must be a mapping")
    procedure_id = raw.get("id")
    title = raw.get("title")
    if not isinstance(procedure_id, str) or not procedure_id.strip():
        raise TreeError(f"{where} needs an id")
    if not isinstance(title, str) or not title.strip():
        raise TreeError(f"{where} needs a title")
    active = raw.get("active")
    if not isinstance(active, bool):
        raise TreeError(f"{where} needs active: true or false")
    benefit_path = raw.get("benefit_path")
    if benefit_path not in BENEFIT_PATHS:
        raise TreeError(f"{where} benefit_path must be medical or dental")
    # A dental-plan check is a later path. The medical questions stay the only active flow.
    if benefit_path == "dental" and active:
        raise TreeError(f"{where} dental benefit path cannot be active yet")
    blurb = raw.get("blurb") or ""
    if not isinstance(blurb, str):
        raise TreeError(f"{where} blurb must be text")
    if not active:
        return {
            "id": procedure_id.strip(),
            "title": title.strip(),
            "blurb": blurb.strip(),
            "benefit_path": benefit_path,
            "active": False,
            "questions": [],
            "results": {},
            "rules": [],
        }
    questions = _questions(raw.get("questions"), where=where)
    question_ids = {item["id"] for item in questions}
    results_raw = raw.get("results")
    if not isinstance(results_raw, dict):
        raise TreeError(f"{where} needs results")
    results: dict[str, Any] = {}
    for result_id in RESULT_IDS:
        spec = results_raw.get(result_id)
        if not isinstance(spec, dict):
            raise TreeError(f"{where} is missing result {result_id}")
        label = spec.get("label")
        if label != RESULT_LABELS[result_id]:
            raise TreeError(f"{where} result {result_id} label must be {RESULT_LABELS[result_id]!r}")
        results[result_id] = {
            "label": label,
            "codes": _codes(spec.get("codes"), where=f"{where} {result_id}"),
            "checklist": _checklist(spec.get("checklist"), where=f"{where} {result_id}"),
            "checklist_from_gaps": bool(spec.get("checklist_from_gaps")),
        }
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise TreeError(f"{where} needs rules")
    rules: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    bill_rule = None
    for rule_index, rule in enumerate(rules_raw):
        spot = f"{where} rule {rule_index}"
        if not isinstance(rule, dict):
            raise TreeError(f"{spot} must be a mapping")
        rule_id = rule.get("id")
        result_id = rule.get("result")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise TreeError(f"{spot} needs an id")
        rule_id = rule_id.strip()
        if rule_id in seen_rules:
            raise TreeError(f"{where} repeats rule {rule_id}")
        seen_rules.add(rule_id)
        if result_id not in RESULT_IDS:
            raise TreeError(f"{spot} has an unknown result")
        when = _clauses(rule["when"], where=f"{spot} when") if rule.get("when") else []
        when_any = _clauses(rule["when_any"], where=f"{spot} when_any") if rule.get("when_any") else []
        if not when and not when_any:
            raise TreeError(f"{spot} needs when or when_any")
        for clause in when + when_any:
            if clause["id"] not in question_ids:
                raise TreeError(f"{spot} mentions unknown question {clause['id']}")
        reason = rule.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise TreeError(f"{spot} needs a reason")
        from_gaps = bool(rule.get("reason_from_gaps"))
        cleaned = {
            "id": rule_id,
            "result": result_id,
            "when": when,
            "when_any": when_any,
            "reason": reason.strip(),
            "reason_from_gaps": from_gaps,
            "reason_citations": _citations(
                rule.get("reason_citations"),
                where=f"{spot} reason",
                allow_empty=from_gaps,
            ),
            "checklist": _checklist(rule.get("checklist"), where=spot),
            "extra_codes": _codes(rule.get("extra_codes"), where=spot),
        }
        rules.append(cleaned)
        if result_id == "bill_medical_first":
            bill_rule = cleaned
    if bill_rule is None:
        raise TreeError(f"{where} needs a bill_medical_first rule")
    if {clause["id"] for clause in bill_rule["when"]} != question_ids:
        raise TreeError(f"{where} bill rule must account for every question")
    return {
        "id": procedure_id.strip(),
        "title": title.strip(),
        "blurb": blurb.strip(),
        "benefit_path": benefit_path,
        "active": True,
        "questions": questions,
        "results": results,
        "rules": rules,
    }


@lru_cache(maxsize=1)
def load_tree() -> dict[str, Any]:
    path = questions_path()
    raw_text = path.read_text(encoding="utf-8")
    if BANNED_PHRASE in raw_text.lower():
        raise TreeError(f"{path} contains {BANNED_PHRASE!r}")
    raw = yaml.safe_load(raw_text)
    _reject_forbidden(raw, where=str(path))
    if not isinstance(raw, dict) or not isinstance(raw.get("procedures"), list):
        raise TreeError(f"{path} needs a procedures list")
    procedures = [_procedure(item, index=index) for index, item in enumerate(raw["procedures"])]
    ids = [item["id"] for item in procedures]
    if len(ids) != len(set(ids)):
        raise TreeError("procedure ids must be unique")
    active = [item for item in procedures if item["active"]]
    if len(active) < 1:
        raise TreeError("at least one procedure must be active")
    return {"procedures": procedures}


def procedure_by_id(procedure_id: str) -> dict[str, Any] | None:
    for item in load_tree()["procedures"]:
        if item["id"] == procedure_id:
            return item
    return None
