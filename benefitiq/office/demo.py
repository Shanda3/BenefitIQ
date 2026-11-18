"""Synthetic demo cases for the cases board.

These are computed from the question tree when a page renders. They are not
inserted into the database. Case ids are obviously synthetic.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from benefitiq.office.decide import decide
from benefitiq.office.present import board_status_for, chip_class, plain_gap

SYNTHETIC_LABEL = "SYNTHETIC demo"
AS_OF = date(2026, 11, 18)

_SLEEP_YES = {
    "sleep_test": "yes",
    "osa_diagnosis": "yes",
    "practitioner_order": "yes",
    "custom_device": "yes",
    "product_list": "yes",
}
_DENTAL_BILL = {
    "linked_service": "yes",
    "care_coordination": "yes",
    "routine_impacted": "no",
    "replacement_device": "no",
}

SEEDS: tuple[dict[str, Any], ...] = (
    {
        "case_id": "SYN-DEMO-0001",
        "procedure_id": "sleep_appliance",
        "as_of": AS_OF,
        "answers": dict(_SLEEP_YES),
        "checked": 7,
    },
    {
        "case_id": "SYN-DEMO-0002",
        "procedure_id": "sleep_appliance",
        "as_of": AS_OF,
        "answers": {**_SLEEP_YES, "sleep_test": "no"},
        "checked": 1,
    },
    {
        "case_id": "SYN-DEMO-0003",
        "procedure_id": "sleep_appliance",
        "as_of": AS_OF,
        "answers": {**_SLEEP_YES, "sleep_test": "not_sure"},
        "checked": 0,
    },
    {
        "case_id": "SYN-DEMO-0004",
        "procedure_id": "medicare_dental",
        "as_of": AS_OF,
        "answers": dict(_DENTAL_BILL),
        "checked": 4,
    },
    {
        "case_id": "SYN-DEMO-0005",
        "procedure_id": "medicare_dental",
        "as_of": AS_OF,
        "answers": {**_DENTAL_BILL, "linked_service": "no"},
        "checked": 0,
    },
    {
        "case_id": "SYN-DEMO-0006",
        "procedure_id": "medicare_dental",
        "as_of": AS_OF,
        "answers": {**_DENTAL_BILL, "care_coordination": "no"},
        "checked": 1,
    },
    {
        "case_id": "SYN-DEMO-0007",
        "procedure_id": "sleep_appliance",
        "as_of": AS_OF,
        "answers": {**_SLEEP_YES, "custom_device": "no"},
        "checked": 0,
    },
    {
        "case_id": "SYN-DEMO-0008",
        "procedure_id": "sleep_appliance",
        "as_of": date(2020, 1, 1),
        "answers": dict(_SLEEP_YES),
        "checked": 0,
    },
)

_BY_ID = {seed["case_id"]: seed for seed in SEEDS}


def _card(seed: dict[str, Any]) -> dict[str, Any]:
    decision = decide(
        seed["procedure_id"],
        seed["answers"],
        as_of=seed["as_of"],
        case_id=seed["case_id"],
    )
    total = len(decision.checklist)
    checked = min(int(seed["checked"]), total)
    status = board_status_for(decision)
    percent = 0 if total == 0 else round(checked * 100 / total)
    return {
        "case_id": seed["case_id"],
        "synthetic_label": SYNTHETIC_LABEL,
        "procedure_id": seed["procedure_id"],
        "procedure_title": decision.procedure_title,
        "as_of": decision.as_of,
        "result_label": decision.result_label,
        "board_status": status,
        "chip_class": chip_class(status),
        "checklist_total": total,
        "checklist_checked": checked,
        "progress_label": f"{checked} of {total}",
        "progress_percent": percent,
        "decision": decision,
        "reason_text": plain_gap(decision.reason.text),
    }


def demo_cards() -> list[dict[str, Any]]:
    return [_card(seed) for seed in SEEDS]


def demo_card(case_id: str) -> dict[str, Any] | None:
    seed = _BY_ID.get(case_id)
    if seed is None:
        return None
    return _card(seed)
