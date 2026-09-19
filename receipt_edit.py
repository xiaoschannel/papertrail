"""Editing a document that is already archived (Receipt Detail's Save), free of Streamlit.

Saving re-files every page of the document under the name the new values produce, rewrites each
page's sidecar and refreshes the smart-match cache, exactly as ``pages/visualize/receipt.py`` does —
with two fixes: every page of a multi-page document is re-filed (Streamlit renamed only the first,
leaving the rest under the old name), and a document keeps its name when the values that build it are
unchanged (Streamlit counted the document's own sidecar as a collision and appended " (2)" on every
save).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from data import delete_sidecar, load_smart_match_cache, read_sidecar, save_smart_match_cache, write_sidecar
from models import ReceiptResult, ReviewDecision, Sidecar, batch_serial_key
from organize_utils import build_accepted_name
from validation import is_date_time_safe_for_archive
from viz_records import page_order


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
    """Why this edit would be refused, or None. Same rules as Review's Accept."""
    if edit.document_type == "receipt":
        missing = [name for name, present in (("cost", edit.cost is not None), ("currency", bool(edit.currency.strip())))
                   if not present]
        if missing:
            return f"Receipt requires: {', '.join(missing)}"
    safe, error = is_date_time_safe_for_archive(edit.date, edit.time)
    if not safe:
        return error
    return None


def _destination(output_path: Path, original_filename: str, decision: ReviewDecision, taken: set[str]) -> Path:
    """Where a page belongs under the new values; ``taken`` are stems already spoken for."""
    folder, base, _seconds = build_accepted_name(decision, original_filename)
    suffix = Path(original_filename).suffix
    name = base
    index = 2
    while name in taken:
        name = f"{base} ({index})"
        index += 1
    taken.add(name)
    return output_path / folder / f"{name}{suffix}"


def _taken_stems(folder: Path, own: set[Path]) -> set[str]:
    """Every name already used in the destination folder, ignoring this document's own pages.

    Counts data files as well as sidecars: a scan whose sidecar is missing is still a file that a
    move would overwrite.
    """
    if not folder.exists():
        return set()
    own_stems = {p.stem for p in own}
    return {entry.stem for entry in folder.iterdir() if entry.is_file() and entry.stem not in own_stems}


def apply_receipt_edit(output_path: Path, page_paths: list[str], edit: ReceiptEdit) -> list[str]:
    """Re-file the document's pages and rewrite their sidecars. Returns the new relative paths.

    Raises ValueError for values that can't be archived and NotEditable when a page has no sidecar.
    """
    error = edit_error(edit)
    if error:
        raise ValueError(error)

    pages: list[tuple[Path, Sidecar]] = []
    for relative in page_paths:
        path = output_path / relative
        sidecar = read_sidecar(path) if path.exists() else None
        if sidecar is None:
            raise NotEditable(f"{relative} has no sidecar; it can't be edited here")
        pages.append((path, sidecar))
    # Scan order decides which page keeps the plain name and which becomes "… (2)".
    pages.sort(key=lambda page: page_order(page[1], page[0].name))

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

    folder, _base, _seconds = build_accepted_name(decision, first.original_filename)
    own = {path for path, _ in pages}
    taken = _taken_stems(output_path / folder, own)

    new_paths: list[str] = []
    for path, sidecar in pages:
        target = _destination(output_path, sidecar.original_filename, decision, taken)
        extraction = sidecar.extraction
        if edit.document_type == "receipt" and isinstance(extraction, ReceiptResult):
            extraction = extraction.model_copy(update={
                "address": edit.address, "language": edit.language, "name": edit.name,
                "date": edit.date, "time": edit.time, "cost": edit.cost or 0.0, "currency": edit.currency,
            })
        updated = sidecar.model_copy(update={"review": decision, "extraction": extraction})
        if target == path:
            write_sidecar(target, updated)
        else:
            if target.exists():  # _taken_stems should have avoided this; never overwrite a scan
                raise FileExistsError(f"{target.relative_to(output_path).as_posix()} already exists")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Sidecar first, then the image, then drop the old sidecar: a failure anywhere leaves the
            # page findable under one name or the other, never as an image the archive can't see.
            write_sidecar(target, updated)
            try:
                shutil.move(str(path), str(target))
            except Exception:
                delete_sidecar(target)
                raise
            delete_sidecar(path)
        new_paths.append(target.relative_to(output_path).as_posix())

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
