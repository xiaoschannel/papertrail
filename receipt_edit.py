"""Editing a document that is already archived (Receipt Detail's Save), free of Streamlit.

Saving re-files every page of the document under the name the new values produce, rewrites each
page's sidecar and refreshes the smart-match cache, exactly as ``pages/visualize/receipt.py`` does —
with two fixes: every page of a multi-page document is re-filed (Streamlit renamed only the first,
leaving the rest under the old name), and a document keeps its name when the values that build it are
unchanged (Streamlit counted the document's own sidecar as a collision and appended " (2)" on every
save). The moving itself is ``document_files``, shared with Workshop and Dedupe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from data import load_smart_match_cache, save_smart_match_cache
from document_files import load_pages, place_document
from models import ReceiptResult, ReviewDecision, Sidecar, batch_serial_key
from review_logic import Draft, accept_error


@dataclass
class ReceiptEdit:
    """The editable fields of an archived document."""

    document_type: str
    name: str
    date: str
    time: str
    cost: float | None
    currency: str
    address: str = ""
    language: str = ""
    comment: str = ""


class NotEditable(Exception):
    """The document has no sidecar on disk, so there is nothing to rewrite."""


def edit_error(edit: ReceiptEdit) -> str | None:
    """Why this edit would be refused, or None — literally Review's Accept rules, so they can't drift."""
    return accept_error(Draft(document_type=edit.document_type, name=edit.name, date=edit.date, time=edit.time,
                              cost=edit.cost, currency=edit.currency))


def apply_receipt_edit(output_path: Path, page_paths: list[str], edit: ReceiptEdit) -> list[str]:
    """Re-file the document's pages and rewrite their sidecars. Returns the new relative paths.

    Raises ValueError for values that can't be archived and NotEditable when a page has no sidecar.
    """
    error = edit_error(edit)
    if error:
        raise ValueError(error)

    try:
        pages = load_pages([output_path / relative for relative in page_paths])
    except LookupError as exc:
        raise NotEditable(str(exc)) from exc

    first = pages[0][1]
    decision = ReviewDecision(
        verdict=first.review.verdict,
        document_type=edit.document_type,
        name=edit.name,
        date=edit.date,
        time=edit.time,
        cost=(edit.cost or 0.0) if edit.document_type == "receipt" else 0.0,
        currency=edit.currency if edit.document_type == "receipt" else "",
        comment=edit.comment,
    )

    def updated(sidecar: Sidecar) -> Sidecar:
        extraction = sidecar.extraction
        if edit.document_type == "receipt" and isinstance(extraction, ReceiptResult):
            extraction = extraction.model_copy(update={
                "address": edit.address, "language": edit.language, "name": edit.name,
                "date": edit.date, "time": edit.time, "cost": edit.cost or 0.0, "currency": edit.currency,
            })
        return sidecar.model_copy(update={"review": decision, "extraction": extraction})

    new_paths = place_document(output_path, pages, decision, sidecar_for=updated)
    _remember_name(output_path, first, edit)
    return new_paths


def _remember_name(output_path: Path, sidecar: Sidecar, edit: ReceiptEdit) -> None:
    """Teach the smart matcher the confirmed name, so Review suggests it next time."""
    extraction = sidecar.extraction
    key = sidecar.document_key
    if key is None and sidecar.batch_id is not None and sidecar.serial is not None:
        key = batch_serial_key(sidecar.batch_id, sidecar.serial)
    if key is None:
        key = sidecar.original_filename
    cache = load_smart_match_cache(output_path)
    cache[key] = {
        "extracted": getattr(extraction, "name", "") or getattr(extraction, "title", ""),
        "confirmed": edit.name,
        "extracted_phone": getattr(extraction, "phone", "") if isinstance(extraction, ReceiptResult) else "",
    }
    save_smart_match_cache(output_path, cache)
