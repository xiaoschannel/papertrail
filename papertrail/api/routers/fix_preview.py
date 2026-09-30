"""Fix Preview (temporary, never in a PR): the extraction-vs-decision fixes on this archive, and the clean-up of
what the old code left behind.

1. Archive used to teach the smart-match cache a "confirmed" name for every document it filed, tossed and marked
   ones too. The code learns only from accepted documents now; Apply removes the entries the others left.
2. The Workshop's form used to start from what the model read. It starts from what Review recorded now: code
   only, nothing to clean up, listed to see that it holds on this archive.
3. The Workshop's record used to keep the form's defaults as "read" ("Receipt" where the model read no name, a
   cost of 0 for a document read as having none). It keeps the read now; Apply rewrites the records already
   written, in the log and on their pages' sidecars, from those sidecars' extraction (which is the read).

The folder's history is the backup. Apply refuses while a file it would change has uncommitted changes, so Undo
(those files back to their last commit) gives back exactly what was there, and Finalize commits them. Redo all is
Undo, then Apply again. Listing reads only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from papertrail.api import ingest_store as store
from papertrail.api.deps import get_output_path, get_root
from papertrail.api.guards import no_job_running
from papertrail.api.routers.workshop import _review_document
from papertrail.archive import history as archive_history
from papertrail.archive.files import MARKED
from papertrail.curate import workshop
from papertrail.data import (
    WORKSHOP_LOG, _workshop_lock, atomic_write_text, build_smart_match_history, load_decisions,
    load_reorganized_state, load_smart_match_cache, load_workshop_log, read_sidecar, save_smart_match_cache,
    sidecar_path_for, write_sidecar,
)
from papertrail.models import DocumentFields, OtherResult, ReceiptResult, Sidecar, Trim, WorkshopRecord, batch_serial_key
from papertrail.viz.records import page_order

router = APIRouter(prefix="/api/dev/fix-preview", tags=["dev"])

CACHE = "smart_match_cache.json"
CACHE_MESSAGE = "Smart match: forget the names tossed and marked documents taught it"
RECORDS_MESSAGE = "Workshop records: keep what the model read, as it read it"


class CleanUp(BaseModel):
    """Where a clean-up stands: ``todo`` items to change; ``uncommitted`` of the files it changes differ from
    their last commit (before Apply that blocks it, after Apply it's what Finalize commits or Undo puts back)."""

    todo: int
    uncommitted: list[str]
    #: Why Apply can't run now ("" when it can).
    blocked: str


class CacheEntryOut(BaseModel):
    key: str
    verdict: Literal["tossed", "marked"]
    extracted: str
    confirmed: str
    #: The "confirmed" name is just what the model read.
    same_as_read: bool
    #: The name stays approved after the clean-up, because an accepted document confirms it too.
    still_approved: bool
    comment: str
    image: str | None
    trim: Trim | None


class PendingOut(BaseModel):
    key: str
    verdict: Literal["tossed", "marked"]
    extracted: str
    confirmed: str
    same_as_read: bool
    still_approved: bool


class SmartMatchOut(BaseModel):
    entries: list[CacheEntryOut]
    #: Entries from tossed or marked documents with no name: removed too, but they never counted.
    unnamed: int
    #: Tossed or marked documents in Review now, which the old Archive would have taught the cache.
    pending: list[PendingOut]
    cache_size: int
    clean_up: CleanUp


class Fields(BaseModel):
    document_type: str
    name: str
    date: str
    time: str
    cost: float | None
    currency: str | None


class MarkedOut(BaseModel):
    key: str
    pages: list[str]
    image: str | None
    trim: Trim | None
    comment: str
    has_read: bool
    reread_pending: bool
    starts: Fields
    recorded: Fields
    differs: list[str]


class RecordOut(BaseModel):
    at: float
    key: str
    filenames: list[str]
    image: str | None
    trim: Trim | None
    reread: bool
    #: Where the read comes from: the page's sidecar (the reread, or the read the document was marked with), or
    #: nothing when the document had no read (the record holds what Review recorded, and keeps it).
    source: Literal["sidecar", "none", "not found"]
    recorded: DocumentFields | None
    literal: DocumentFields | None
    differs: list[str]
    corrected_now: list[str]
    corrected_fixed: list[str]


class RecordsOut(BaseModel):
    records: list[RecordOut]
    clean_up: CleanUp


class FixPreviewOut(BaseModel):
    smart_match: SmartMatchOut
    marked: list[MarkedOut]
    records: RecordsOut


FIELDS = ("document_type", "name", "date", "time", "cost", "currency")


def _read_name(extraction) -> str:
    if isinstance(extraction, ReceiptResult):
        return extraction.name
    if isinstance(extraction, OtherResult):
        return extraction.title or ""
    return ""


def _differs(a, b) -> list[str]:
    return [f for f in FIELDS if getattr(a, f) != getattr(b, f)]


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _clean_up(root: Path, todo: int, paths: list[str]) -> CleanUp:
    changed = archive_history.changed_paths(root, paths)
    if changed is None:
        return CleanUp(todo=todo, uncommitted=[], blocked="The folder has no history yet, so nothing could be undone.")
    uncommitted = sorted(changed)
    blocked = ("Nothing to do." if todo == 0 else
               f"{len(uncommitted)} of the files it changes have uncommitted changes: commit or discard them on the "
               "History page first, so Undo can give back exactly what is there." if uncommitted else "")
    return CleanUp(todo=todo, uncommitted=uncommitted, blocked=blocked)


# --- 1. smart match ------------------------------------------------------------------------------------------
def _keys(sidecar: Sidecar) -> set[str]:
    keys = {sidecar.document_key} if sidecar.document_key else set()
    if sidecar.batch_id is not None and sidecar.serial is not None:
        keys.add(batch_serial_key(sidecar.batch_id, sidecar.serial))
    return keys


def _filed(output_path: Path, folder: str) -> dict[str, tuple[Sidecar, str]]:
    """Each document key in ``folder`` (tossed/ or marked/) -> its first page's sidecar and relative path."""
    pages: dict[str, list[tuple[Sidecar, str]]] = {}
    directory = output_path / folder
    if not directory.is_dir():
        return {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() == ".json" or path.name.endswith(".enhanced.png"):
            continue
        sidecar = read_sidecar(path)
        if sidecar is None:
            continue
        for key in _keys(sidecar):
            pages.setdefault(key, []).append((sidecar, f"{folder}/{path.name}"))
    return {key: min(found, key=lambda page: page_order(page[0], page[1])) for key, found in pages.items()}


def _unaccepted(output_path: Path) -> dict[str, tuple[str, tuple[Sidecar, str]]]:
    return {**{k: ("tossed", v) for k, v in _filed(output_path, "tossed").items()},
            **{k: ("marked", v) for k, v in _filed(output_path, MARKED).items()}}


def _smart_match(root: Path, output_path: Path) -> SmartMatchOut:
    cache = load_smart_match_cache(output_path)
    extractions, decisions = store.extractions(output_path), load_decisions(output_path)
    filed = _unaccepted(output_path)
    removed = {k for k in cache if k in filed}
    approved_after = {row.confirmed for row in build_smart_match_history(
        extractions, decisions, {k: v for k, v in cache.items() if k not in removed})}

    entries, unnamed = [], 0
    for key in sorted(removed):
        entry = cache[key]
        confirmed = entry.get("confirmed", "")
        if not confirmed:
            unnamed += 1
            continue
        verdict, (sidecar, rel) = filed[key]
        extracted = entry.get("extracted", "")
        entries.append(CacheEntryOut(
            key=key, verdict=verdict, extracted=extracted, confirmed=confirmed, same_as_read=confirmed == extracted,
            still_approved=confirmed in approved_after, comment=sidecar.review.comment, image=rel, trim=sidecar.trim))

    pending = []
    for key, decision in sorted(decisions.items()):
        if decision.verdict == "accepted" or decision.sliced or not decision.name or key not in extractions:
            continue
        extracted = _read_name(extractions[key])
        pending.append(PendingOut(key=key, verdict=decision.verdict, extracted=extracted, confirmed=decision.name,
                                  same_as_read=decision.name == extracted,
                                  still_approved=decision.name in approved_after))
    return SmartMatchOut(entries=entries, unnamed=unnamed, pending=pending, cache_size=len(cache),
                         clean_up=_clean_up(root, len(removed), [_relative(root, output_path / CACHE)]))


# --- 2. the Workshop form ------------------------------------------------------------------------------------
def _marked(output_path: Path) -> list[MarkedOut]:
    out = []
    for document in workshop.marked_documents(output_path):
        reread = workshop.rereads.get(document)
        shown = _review_document(output_path, document, reread)
        d = shown.defaults
        starts = Fields(document_type=d.document_type, name=shown.initial_name, date=d.date, time=d.time,
                        cost=d.cost if d.document_type == "receipt" else None,
                        currency=d.currency if d.document_type == "receipt" else None)
        review = document.first.review
        receipt = review.document_type == "receipt"
        recorded = Fields(document_type=review.document_type, name=review.name, date=review.date, time=review.time,
                          cost=review.cost if receipt else None, currency=review.currency if receipt else None)
        out.append(MarkedOut(
            key=document.key, pages=document.filenames, image=f"{MARKED}/{document.pages[0].name}",
            trim=document.first.trim, comment=review.comment, has_read=document.first.extraction is not None,
            reread_pending=reread is not None, starts=starts, recorded=recorded, differs=_differs(starts, recorded)))
    return out


# --- 3. Workshop records -------------------------------------------------------------------------------------
def _record_pages(output_path: Path) -> dict[float, tuple[Sidecar, str]]:
    _tossed, accepted = load_reorganized_state(output_path)
    return {sidecar.workshop.at: (sidecar, rel) for sidecar, rel in accepted.values() if sidecar.workshop}


def _fixed(record: WorkshopRecord, sidecar: Sidecar | None) -> WorkshopRecord:
    """The record as the fixed code writes it: the read from the page's sidecar (unchanged without one)."""
    if sidecar is None or sidecar.extraction is None:
        return record
    read = workshop.read_fields(sidecar.extraction)
    return record.model_copy(update={"read": read, "corrected": workshop.corrected(read, record.accepted)})


def _record_paths(root: Path, output_path: Path, pages: dict[float, tuple[Sidecar, str]]) -> list[str]:
    return [_relative(root, output_path / WORKSHOP_LOG)] + sorted(
        _relative(root, sidecar_path_for(output_path / rel)) for _, rel in pages.values() if rel)


def _records(root: Path, output_path: Path) -> RecordsOut:
    pages = _record_pages(output_path)
    out = []
    for record in load_workshop_log(output_path):
        sidecar, rel = pages.get(record.at, (None, None))
        literal = workshop.read_fields(sidecar.extraction) if sidecar and sidecar.extraction else None
        fixed = _fixed(record, sidecar)
        out.append(RecordOut(
            at=record.at, key=record.key, filenames=record.filenames, image=rel or None,
            trim=sidecar.trim if sidecar else None, reread=record.reread is not None,
            source="not found" if sidecar is None else ("sidecar" if literal else "none"),
            recorded=record.read, literal=literal, differs=_differs(record.read, literal) if literal and record.read else [],
            corrected_now=record.corrected, corrected_fixed=fixed.corrected))
    todo = sum(1 for r in out if r.differs or r.corrected_now != r.corrected_fixed)
    return RecordsOut(records=out, clean_up=_clean_up(root, todo, _record_paths(root, output_path, pages)))


# --- endpoints -----------------------------------------------------------------------------------------------
@router.get("", response_model=FixPreviewOut)
def fix_preview(root: Path = Depends(get_root), output_path: Path = Depends(get_output_path)):
    """Each fix on this archive, and where its clean-up stands. Reads only."""
    return FixPreviewOut(smart_match=_smart_match(root, output_path), marked=_marked(output_path),
                         records=_records(root, output_path))


def _refuse_if_blocked(clean_up: CleanUp) -> None:
    if clean_up.blocked:
        raise HTTPException(status_code=409, detail=clean_up.blocked)


@router.post("/smart-match/apply", response_model=FixPreviewOut)
def apply_smart_match(root: Path = Depends(get_root), output_path: Path = Depends(get_output_path)):
    """Remove every cache entry a tossed or marked document left."""
    with no_job_running("clean up the smart-match cache"), store.decisions_lock:
        _refuse_if_blocked(_smart_match(root, output_path).clean_up)
        filed = _unaccepted(output_path)
        cache = load_smart_match_cache(output_path)
        save_smart_match_cache(output_path, {k: v for k, v in cache.items() if k not in filed})
    return fix_preview(root, output_path)


@router.post("/records/apply", response_model=FixPreviewOut)
def apply_records(root: Path = Depends(get_root), output_path: Path = Depends(get_output_path)):
    """Rewrite every Workshop record whose read isn't the model's, in the log and on its page's sidecar."""
    with no_job_running("rewrite the Workshop records"), _workshop_lock:
        _refuse_if_blocked(_records(root, output_path).clean_up)
        pages = _record_pages(output_path)
        log = output_path / WORKSHOP_LOG
        lines = []
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = WorkshopRecord.model_validate_json(line)
            sidecar, rel = pages.get(record.at, (None, None))
            fixed = _fixed(record, sidecar)
            lines.append(fixed.model_dump_json(exclude_none=True) if fixed != record else line)
            if fixed != record and sidecar is not None and rel:
                write_sidecar(output_path / rel, sidecar.model_copy(update={"workshop": fixed}))
        atomic_write_text(log, "".join(f"{line}\n" for line in lines))
    return fix_preview(root, output_path)


def _paths(which: str, root: Path, output_path: Path) -> list[str]:
    if which == "smart-match":
        return [_relative(root, output_path / CACHE)]
    return _record_paths(root, output_path, _record_pages(output_path))


@router.post("/{which}/undo", response_model=FixPreviewOut)
def undo(which: Literal["smart-match", "records"], root: Path = Depends(get_root),
         output_path: Path = Depends(get_output_path)):
    """Put the files the clean-up changes back to their last commit."""
    with no_job_running("undo the clean-up"):
        archive_history.discard_changes(root, _paths(which, root, output_path))
    return fix_preview(root, output_path)


@router.post("/{which}/finalize", response_model=FixPreviewOut)
def finalize(which: Literal["smart-match", "records"], root: Path = Depends(get_root),
             output_path: Path = Depends(get_output_path)):
    """Commit the files the clean-up changed: it is done, and the History page can still take it back."""
    with no_job_running("commit the clean-up"):
        archive_history.commit(root, CACHE_MESSAGE if which == "smart-match" else RECORDS_MESSAGE,
                               _paths(which, root, output_path))
    return fix_preview(root, output_path)
