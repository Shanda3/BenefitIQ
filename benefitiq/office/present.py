"""Screen copy for the office workspace.

The citation check still returns the internal status ``not found``.
Pages show that gap in plain words. ``board_status`` maps a result onto
the three case columns.
"""

from __future__ import annotations

from typing import Any

PLAIN_GAP = "Our sources don't cover this yet. Confirm with the payer."
LICENSED_CRITERIA = "This payer's criteria aren't public. Confirm with the payer."


def payer_gap(payer_name: str) -> str:
    """Plain sentence when a chosen plan has no policy for this procedure."""
    return f"Our sources don't cover this for {payer_name} yet. Confirm with the payer."

READY = "Ready to bill"
NEEDS_DOCS = "Needs documentation"
NOT_COVERED = "Not covered or not found"
BOARD_COLUMNS = (READY, NEEDS_DOCS, NOT_COVERED)

_CHIP = {
    READY: "chip-ready",
    NEEDS_DOCS: "chip-docs",
    NOT_COVERED: "chip-none",
}


def plain_gap(text: str | None) -> str:
    """Visible sentence for an uncited line. Every other string is unchanged."""
    if text == "not found":
        return PLAIN_GAP
    return text or ""


def board_status(
    result_label: str,
    *,
    documentation_missing: bool = False,
    uncited: bool = False,
) -> str:
    """Map a result onto a cases-board column.

    Need more info, or any documentation gap, is Needs documentation.
    Probably not medical, or a result whose reason is uncited, is
    Not covered or not found.
    Bill medical first, with the reason cited and no documentation gap,
    is Ready to bill.
    """
    if result_label == "Need more info" or documentation_missing:
        return NEEDS_DOCS
    if result_label == "Probably not medical" or uncited:
        return NOT_COVERED
    if result_label == "Bill medical first":
        return READY
    return NOT_COVERED


def board_status_for(decision: Any) -> str:
    return board_status(
        decision.result_label,
        documentation_missing=bool(decision.gaps),
        uncited=decision.reason.text == "not found",
    )


def chip_class(status: str) -> str:
    return _CHIP.get(status, "chip-none")
