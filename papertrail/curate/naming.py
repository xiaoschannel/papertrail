"""Naming the documents filed under no name (the Unnamed page).

A document is *unnamed* when it is filed under a name the Review form falls back to ("Receipt",
"Document"): the extractor found no name on it and nobody typed one in. Naming one here rewrites the name it
is filed under and re-files its pages (the name is part of the file name), and nothing else: the extraction
keeps what the model read, so a document named here joins the *named by hand*.

The named by hand are the documents the extractor found no name on (the form started from a placeholder)
that are filed under a real name: the name a person gave, beside a scan the model couldn't name. They are
what a better extraction prompt can be tried against.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from papertrail.archive.files import ensure_movable, load_pages, place_document
from papertrail.data import read_sidecar, remember_confirmed_names
from papertrail.models import OtherResult, ReceiptResult, Sidecar, Trim
from papertrail.review.logic import PLACEHOLDER_NAMES, form_defaults, is_placeholder_name

# ``load_reorganized_state``'s result, from the API's cache.
ArchiveState = tuple[set[str], dict[str, tuple[Sidecar, str]]]


class NamingDocument(BaseModel):
    """One archived document as the Unnamed page shows it."""

    #: the document's key, as Receipt Detail takes it (``file``)
    filename: str
    #: its first page in the archive, and the band of it that is shown
    path: str
    trim: Trim | None
    pages: int
    document_type: str
    #: the name it is filed under, and whether that is no name (a placeholder)
    name: str
    unnamed: bool
    #: what the extractor read as its name ("" for nothing): a document can be read with a name and still
    #: be filed unnamed, when the one read was no name at all
    read: str
    date: str
    time: str
    cost: float
    currency: str


def extracted_name(sidecar: Sidecar) -> str:
    extraction = sidecar.extraction
    if isinstance(extraction, ReceiptResult):
        return extraction.name
    if isinstance(extraction, OtherResult):
        return extraction.title
    return ""


def read_without_a_name(sidecar: Sidecar) -> bool:
    """Whether the extractor found no name: the Review form started this document from a placeholder.

    A document filed without an extraction (from before one was kept) can't say, so it isn't counted."""
    return sidecar.extraction is not None and is_placeholder_name(form_defaults(sidecar.extraction).name)


def _order(document: NamingDocument) -> tuple:
    return (document.date == "", document.date, document.time, document.filename)   # undated last


def _documents(records: pd.DataFrame, state: ArchiveState) -> list[tuple[NamingDocument, Sidecar]]:
    """Every archived document with its first page's sidecar. ``records`` is ``build_viz_records``' frame
    (one row per document) and ``state`` the archive it was read from."""
    if records.empty:
        return []
    by_path = {relative: sidecar for sidecar, relative in state[1].values()}
    return [(NamingDocument(
                filename=row["filename"], path=row["path"], trim=row["trim"], pages=len(row["paths"]),
                document_type=row["document_type"], name=row["name"], unnamed=is_placeholder_name(row["name"]),
                read=extracted_name(sidecar),
                date=row["date"], time=row["time"], cost=float(row["cost"]), currency=row["currency"]), sidecar)
            for row in records.to_dict("records") if (sidecar := by_path.get(row["path"])) is not None]


def naming_documents(records: pd.DataFrame, state: ArchiveState) -> tuple[list[NamingDocument], list[NamingDocument]]:
    """(unnamed, named by hand), each oldest first, undated last."""
    unnamed: list[NamingDocument] = []
    by_hand: list[NamingDocument] = []
    for document, sidecar in _documents(records, state):
        if document.unnamed:
            unnamed.append(document)
        elif read_without_a_name(sidecar):
            by_hand.append(document)
    return sorted(unnamed, key=_order), sorted(by_hand, key=_order)


def documents_by_key(records: pd.DataFrame, state: ArchiveState, keys: list[str]) -> dict[str, NamingDocument]:
    """These documents as they are filed now (a key the archive doesn't have is left out)."""
    wanted = set(keys)
    return {document.filename: document for document, _ in _documents(records, state) if document.filename in wanted}


class NotNamed(Exception):
    """Nothing was named, and why (a document isn't where the listing said: the archive changed since)."""


class Naming(BaseModel):
    """How a naming went: how many of the documents, from the first, were named, and why the next one wasn't."""

    named: int
    error: str | None = None


def _same_page(a: Sidecar, b: Sidecar) -> bool:
    return (a.original_filename, a.batch_id, a.serial) == (b.original_filename, b.batch_id, b.serial)


def _where_now(pages: list[tuple[Path, Sidecar]]) -> list[Path]:
    """Each page's file now: where it was listed, or under another number in the same folder.

    Moving a document closes the gap it leaves in its name's numbers (``archive.files.close_gaps``), so
    when several documents share a name ("… Receipt", "… Receipt (2)"), naming the first renumbers the
    ones after it. A page is known by its scan, which no renumbering changes."""
    found = []
    for path, listed in pages:
        here = read_sidecar(path) if path.exists() else None
        if here is None or not _same_page(here, listed):
            path = next((candidate.with_suffix(path.suffix) for candidate in path.parent.glob("*.json")
                         if candidate.with_suffix(path.suffix).exists()
                         and _same_page(Sidecar.model_validate_json(candidate.read_text(encoding="utf-8")), listed)),
                        path)          # not there either: load_pages says so
        found.append(path)
    return found


def name_documents(output_path: Path, documents: list[list[str]], name: str) -> Naming:
    """File each document (its pages' paths in the archive) under ``name``, in order.

    Only the name changes: the review's other values and the extraction stay as they are. Every page must
    be where it was listed and free to move before anything moves (``NotNamed``, ``FileInUse``). A document
    that then fails to move is put back as it was (``place_document``), and the ones after it are left:
    the result says how many were named, and why the next wasn't. The smart-match cache learns each name."""
    name = name.strip()
    if not name:
        raise ValueError("Type a name.")
    try:
        listed = [load_pages([output_path / relative for relative in paths]) for paths in documents]
    except LookupError as exc:
        raise NotNamed(f"{exc}: the archive has changed since the page was opened. Open it again.") from exc
    ensure_movable([path for pages in listed for path, _ in pages])
    named: list[tuple[Sidecar, str]] = []
    try:
        for index, pages in enumerate(listed):
            try:
                pages = load_pages(_where_now(pages))
            except LookupError as exc:
                return Naming(named=index, error=str(exc))
            decision = pages[0][1].review.model_copy(update={"name": name})
            try:
                place_document(output_path, pages, decision, sidecar_for=lambda sidecar, decision=decision:
                               sidecar.model_copy(update={"review": decision}))
            except OSError as exc:
                return Naming(named=index, error=str(exc))
            named.append((pages[0][1], name))
    finally:
        remember_confirmed_names(output_path, named)
    return Naming(named=len(listed))
