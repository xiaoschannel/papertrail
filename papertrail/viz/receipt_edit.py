"""Editing a document that is already archived (Receipt Detail's Save).

Saving re-files every page of the document under the name the new values produce, rewrites each
page's sidecar and refreshes the smart-match cache. Every page of a multi-page document is re-filed,
so none is left under the old name, and a document keeps its name when the values that build it are
unchanged: its own sidecar doesn't count as a collision, or every save would append " (2)". The
moving itself is ``archive.files``, shared with Workshop and Dedupe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from papertrail.data import remember_confirmed_names
from papertrail.archive.files import load_pages, place_document
from papertrail.models import ReceiptResult, ReviewDecision, Sidecar
from papertrail.review.logic import Draft, accept_error


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
    remember_confirmed_names(output_path, [(first, edit.name)])   # teach the smart matcher the name
    return new_paths

