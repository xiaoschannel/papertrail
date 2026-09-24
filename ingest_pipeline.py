"""The ingest pipeline steps — File Index, OCR, Parse, Archive — as plain functions.

Each step has a ``plan_*`` (what would happen, for the page to show) and, for the long steps, a
``run_*`` that takes a progress context and the model to use as arguments, so tests run them with
fakes (no GPU, no network).

Guarantees the steps keep:
- OCR and Parse never drop results for items outside the current run: OCR writes only the batch files
  (``ocr/<batch_id>.json``) of the batches it holds, and Parse merges into extractions.json rather than
  rewriting it from a filtered or emptied dict.
- Reprocessing replaces results as each item finishes, so cancelling keeps the old results of items
  not yet redone.
- OCR runs the second "structured" pass only for providers that return grounding boxes.
- Parse failures are reported per item; Archive reports per-file errors and only finalizes (marks
  batches archived, deletes the mid-ingest files) when every file was archived.
- The milestones commit what they produced to the folder's history (archive_history): File Index the
  index and the new scans, Finalize on Fix Rotation, Slice and Group that step's files for every batch
  still being ingested, OCR and Parse their results (however the run ended), Archive the filed pages and
  then the scans it removed, which it does only once their pages are in the last commit.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from openai import RateLimitError

import archive_history
import run_log
from data import (
    build_document_index,
    clear_extractions_decisions_for_batch,
    delete_sidecar,
    drop_extractions,
    drop_model_runs,
    filed_scan_pages,
    sidecar_path_for,
    load_decisions,
    load_document_groups,
    load_extractions,
    load_model_runs,
    load_ocr_batch,
    load_ocr_results,
    load_trims,
    load_smart_match_cache,
    replace_groups_for_batch,
    save_decisions,
    merge_extractions,
    merge_model_runs,
    save_ocr_batch,
    MIGRATED_OCR,
    OCR_DIR,
    OCR_RUNS,
    EXTRACTION_RUNS,
    save_scan_index,
    save_smart_match_cache,
    scan_organized_filenames,
    set_trim,
    TRIMS,
    write_sidecar,
    ROTATION_DECISIONS,
    ROTATION_LOG,
    WORKSHOP_LOG,
    load_rotation_decisions,
)
from deskew import straighten_file, straightened_trim
from document_grouping import build_display_state, rotate_file_upright, split_groups_at_tossed_boundaries
import extraction
import slicing
from extraction import call_extractor, cost_of
from grounding import parse_grounding_output
from indexing_schemes import SCHEMES, parse_canon_filename
from rate_budget import duration
from models import (
    DocumentExtraction,
    DocumentIndex,
    DocumentKey,
    ModelRun,
    OcrResult,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    ScanBatch,
    ScanIndex,
    Sidecar,
    Trim,
    TokenUse,
    batch_serial_key,
    filename_to_batch_serial,
    iter_indexed_files,
    load_scan_index,
    parse_batch_serial_key,
    turned_trim,
)
from organize_utils import plan_accepted_destinations, scan_existing_names
from scan_enhance import trimmed_file
from settings import ARCHIVE_DIR, IMAGE_EXTENSIONS, SCANS_DIR, root_of


class Progress(Protocol):
    """What the long steps report to (api.jobs.JobContext in the web app)."""

    @property
    def cancelled(self) -> bool: ...
    @property
    def job_id(self) -> str: ...
    def set_total(self, total: int) -> None: ...
    def tick(self, ok: bool = True, item: str = "", error: str = "") -> None: ...
    def say(self, message: str) -> None: ...
    def record(self, run: ModelRun) -> None: ...


class OcrProvider(Protocol):
    def run(self, path: Path, structured: bool = False) -> str: ...


ExtractFn = Callable[..., DocumentExtraction]


def _short_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _outcome(verb: str, done: int, failed: int, total: int, unit: str) -> str:
    """A job's closing line. Attempts alone read as success, so failures are always named."""
    if failed and failed == done:
        return f"Every one of the {failed} {unit}(s) failed. Nothing usable was written."
    tail = f" {failed} failed." if failed else ""
    return f"{verb} {done - failed} of {total} {unit}(s).{tail}"


def _load_index(output_path: Path) -> ScanIndex | None:
    return load_scan_index(output_path) if (output_path / "batches.json").exists() else None


# =====================================================================================
# File Index
# =====================================================================================
def input_images(input_path: Path) -> list[str]:
    """Image filenames directly in the scan-input folder, sorted."""
    return sorted(p.name for p in input_path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


@dataclass
class IndexProposal:
    scheme: str
    image_count: int
    indexed_count: int
    unindexed_count: int
    existing_batches: int
    batches: list[ScanBatch]
    skipped: list[str]
    warnings: list[str]
    error: str | None
    offending: list[str]
    token: str


def propose_index(input_path: Path, output_path: Path, scheme_name: str) -> IndexProposal:
    """New batches the indexing scheme would create from the unindexed input images."""
    if scheme_name not in SCHEMES:
        raise ValueError(f"unknown indexing scheme: {scheme_name}")
    filenames = input_images(input_path)
    existing = _load_index(output_path)
    indexed = set(filename_to_batch_serial(existing)) if existing else set()
    unindexed = [fn for fn in filenames if fn not in indexed]

    batches: list[ScanBatch] = []
    skipped: list[str] = []
    warnings: list[str] = []
    error: str | None = None
    offending: list[str] = []
    if unindexed:
        relative, skipped, warnings = SCHEMES[scheme_name](unindexed)
        if scheme_name == "Canon ImageFormula" and existing and existing.batches and relative:
            last = existing.batches[-1]
            last_end = datetime.strptime(last.end_datetime, "%Y-%m-%d %H:%M:%S")
            for fn in unindexed:
                if fn in skipped:
                    continue
                parsed = parse_canon_filename(fn)
                if parsed and parsed[0] < last_end:
                    offending.append(fn)
            if offending:
                error = (f"{len(offending)} unindexed file(s) have timestamps before the last batch's end "
                         f"({last.end_datetime}). This indicates files were missed. "
                         f"Delete batches.json and rebuild from scratch to fix.")
        if error is None:
            first_id = existing.batches[-1].batch_id + 1 if existing and existing.batches else 1
            batches = [ScanBatch(batch_id=first_id + i, start_datetime=b.start_datetime,
                                 end_datetime=b.end_datetime, files=b.files)
                       for i, b in enumerate(relative)]

    token = hashlib.sha1(json.dumps(
        [scheme_name, [b.model_dump() for b in batches]], sort_keys=True, default=str).encode()).hexdigest()
    return IndexProposal(
        scheme=scheme_name, image_count=len(filenames), indexed_count=len(indexed), unindexed_count=len(unindexed),
        existing_batches=len(existing.batches) if existing else 0, batches=batches, skipped=skipped,
        warnings=warnings, error=error, offending=offending, token=token,
    )


class StaleProposal(Exception):
    """The input folder or index changed since the proposal was shown."""


def confirm_index(input_path: Path, output_path: Path, scheme_name: str, token: str) -> list[ScanBatch]:
    """Append the proposed batches to batches.json, if the proposal is still the one the user saw."""
    proposal = propose_index(input_path, output_path, scheme_name)
    if proposal.token != token:
        raise StaleProposal("The input folder or index changed; review the new proposal.")
    if proposal.error:
        raise ValueError(proposal.error)
    if not proposal.batches:
        raise ValueError("There are no new batches to add.")
    existing = _load_index(output_path)
    save_scan_index(output_path, ScanIndex(batches=(existing.batches if existing else []) + proposal.batches))
    # milestone: the index and the new batches' scans, as they came in
    ids = [b.batch_id for b in proposal.batches]
    scans = sum(len(b.files) for b in proposal.batches)
    archive_history.commit(root_of(output_path),
                           f"File Index: {_batches(ids)} ({scans} scan{'s' if scans != 1 else ''})",
                           [f"{ARCHIVE_DIR}/batches.json",
                            *(f"{SCANS_DIR}/{fn}" for b in proposal.batches for fn in b.files.values())])
    return proposal.batches


def _batches(ids: list[int]) -> str:
    """``batch 3``, ``batches 3 and 4``, ``batches 2, 3 and 4``: for a commit message."""
    if len(ids) == 1:
        return f"batch {ids[0]}"
    return "batches " + ", ".join(map(str, ids[:-1])) + f" and {ids[-1]}"


# =====================================================================================
# Document grouping (File Index)
# =====================================================================================
@dataclass
class GroupingPage:
    key: str
    serial: int
    filename: str
    tossed: bool
    #: the sheet this page was cut from ("batch:serial") and its cell, for a crop
    crop_of: str | None = None
    cell: list[int] | None = None
    #: a sheet cut into crops (tossed, and only unslicing brings it back)
    sliced: bool = False
    trim: Trim | None = None


@dataclass
class GroupingState:
    batch: ScanBatch
    pages: list[GroupingPage]
    display_keys: list[str]
    active_links: list[bool]
    saved_groups: list[list[str]]


def _non_archived_batch(output_path: Path, batch_id: int) -> tuple[ScanIndex, ScanBatch]:
    index = _load_index(output_path)
    batch = next((b for b in index.batches if b.batch_id == batch_id), None) if index else None
    if batch is None or batch.archived:
        raise KeyError(f"no unarchived batch {batch_id}")
    return index, batch


def _batch_keys(batch: ScanBatch) -> list[str]:
    return [batch_serial_key(batch.batch_id, serial) for serial in sorted(batch.files)]


def _tossed_keys(output_path: Path, keys: list[str], decisions: dict[str, ReviewDecision]) -> set[str]:
    index = build_document_index(output_path, set(keys))
    tossed = set()
    for key in keys:
        decision = decisions.get(str(index.key_to_doc_key(key)))
        if decision is not None and decision.verdict == "tossed":
            tossed.add(key)
    return tossed


def _saved_batch_groups(output_path: Path, batch: ScanBatch, keys: list[str]) -> list[list[str]]:
    key_set = set(keys)
    return [g for g in load_document_groups(output_path).groups
            if g and (DocumentKey.parse(g[0]) or DocumentKey(0, 0, 0)).batch_id == batch.batch_id
            and all(k in key_set for k in g)]


def _active_saved_groups(saved: list[list[str]], keys: list[str], tossed: set[str]) -> list[list[str]]:
    """The saved multi-page groups as the grouping editor sees them: tossed pages left out."""
    return [g for g in split_groups_at_tossed_boundaries(saved, keys, tossed) if len(g) > 1]


def grouping_state(output_path: Path, batch_id: int) -> GroupingState:
    """``saved_groups`` leaves tossed pages out, so it compares equal to an unedited editor's groups."""
    _, batch = _non_archived_batch(output_path, batch_id)
    keys = _batch_keys(batch)
    tossed = _tossed_keys(output_path, keys, load_decisions(output_path))
    saved = _saved_batch_groups(output_path, batch, keys)
    display_keys, _, links = build_display_state(keys, saved, tossed)
    decisions = load_decisions(output_path)
    trims = load_trims(output_path)
    pages = []
    for k, s in zip(keys, sorted(batch.files)):
        of = batch.slices.get(s)
        pages.append(GroupingPage(
            key=k, serial=s, filename=batch.files[s], tossed=k in tossed,
            crop_of=batch_serial_key(batch.batch_id, of.sheet) if of else None, cell=[of.row, of.col] if of else None,
            sliced=bool(decisions.get(k) and decisions[k].sliced), trim=trims.get(k)))
    return GroupingState(batch=batch, pages=pages, display_keys=display_keys, active_links=links,
                         saved_groups=_active_saved_groups(saved, keys, tossed))


def check_grouping(output_path: Path, batch_id: int, groups: list[list[str]]) -> None:
    """Raise (KeyError, ValueError) if ``groups`` can't be saved as the batch's grouping; see save_grouping."""
    _, batch = _non_archived_batch(output_path, batch_id)
    keys = _batch_keys(batch)
    _check_groups(batch, groups, set(keys) - _tossed_keys(output_path, keys, load_decisions(output_path)))


def _check_groups(batch: ScanBatch, groups: list[list[str]], active: set[str]) -> None:
    crops = {batch_serial_key(batch.batch_id, s) for s in batch.slices}
    seen: set[str] = set()
    for group in groups:
        for key in group:
            if key not in active:
                raise ValueError(f"{key} is not an active page of batch {batch.batch_id}")
            if len(group) > 1 and key in crops:
                raise ValueError(f"{key} is a crop of a sliced sheet; a crop is a document of its own")
            if key in seen:
                raise ValueError(f"{key} appears in more than one group")
            seen.add(key)


def save_grouping(output_path: Path, batch_id: int, groups: list[list[str]]) -> bool:
    """Save a batch's page grouping (groups of active pages). Returns False (and changes nothing) if the
    multi-page groups are unchanged. A change clears the batch's extractions and review decisions,
    because documents' keys and contents changed — except tosses: a tossed document keeps its
    decision and, if it was a saved group, its group (tossed pages can't be regrouped)."""
    _, batch = _non_archived_batch(output_path, batch_id)
    keys = _batch_keys(batch)
    tossed = _tossed_keys(output_path, keys, load_decisions(output_path))
    active = set(keys) - tossed
    _check_groups(batch, groups, active)
    multi = [g for g in groups if len(g) > 1]
    saved = _saved_batch_groups(output_path, batch, keys)
    if sorted(multi) == sorted(_active_saved_groups(saved, keys, tossed)):  # group order is irrelevant
        return False
    tossed_groups = [g for g in saved if any(k in tossed for k in g)]
    replace_groups_for_batch(output_path, batch_id, multi + tossed_groups)
    clear_extractions_decisions_for_batch(output_path, batch_id)
    return True


def set_page_tossed(output_path: Path, key: str, tossed: bool) -> None:
    """Toss a page (its whole document) from File Index, or recover it."""
    parsed = parse_batch_serial_key(key)
    if parsed is None:
        raise KeyError(f"not a page key: {key}")
    _, batch = _non_archived_batch(output_path, parsed[0])
    keys = _batch_keys(batch)
    if key not in keys:
        raise KeyError(f"no page {key}")
    doc_key = str(build_document_index(output_path, set(keys)).key_to_doc_key(key))
    decisions = load_decisions(output_path)
    if doc_key in decisions and decisions[doc_key].sliced:
        raise ValueError(f"{key} is a sliced sheet; unslice it on the Slice page instead.")
    if tossed:
        decisions[doc_key] = ReviewDecision(verdict="tossed", document_type="corrupted", name="", date="", time="",
                                            cost=0.0, currency="")
    elif doc_key in decisions:
        del decisions[doc_key]
    else:
        return
    save_decisions(output_path, decisions)


def _unarchived_page(output_path: Path, key: str) -> tuple[ScanBatch, int, str]:
    """An unarchived page by key: its batch, serial and filename (KeyError for a page that isn't one)."""
    parsed = parse_batch_serial_key(key)
    if parsed is None:
        raise KeyError(f"not a page key: {key}")
    _, batch = _non_archived_batch(output_path, parsed[0])
    filename = batch.files.get(parsed[1])
    if filename is None:
        raise KeyError(f"no page {key}")
    return batch, parsed[1], filename


def _is_sliced_sheet(output_path: Path, batch: ScanBatch, serial: int, key: str) -> bool:
    """A sheet cut into crops: it has a grid, or is tossed as sliced."""
    decision = load_decisions(output_path).get(key)
    return serial in batch.grids or (decision is not None and decision.sliced)


def _forget_reading(output_path: Path, batch: ScanBatch, key: str) -> None:
    """A page's scan was turned: what was read from it no longer holds. Its OCR goes (the next OCR run reads
    it again, cheaply) and so does its document's extraction (Parse then extracts it again from the new
    reading), each with the record of the run that made it. A review decision stays: it is a person's."""
    results = load_ocr_batch(output_path, batch.batch_id)
    if key in results:
        del results[key]
        save_ocr_batch(output_path, batch.batch_id, results)
    drop_model_runs(output_path, OCR_RUNS, {key})
    doc_key = str(build_document_index(output_path, set(_batch_keys(batch))).key_to_doc_key(key))
    drop_extractions(output_path, {doc_key})
    drop_model_runs(output_path, EXTRACTION_RUNS, {doc_key})


def rotate_page_image(output_path: Path, input_path: Path, key: str, top_points: str) -> None:
    """Rotate an unarchived page's scan in place given where its top currently points.

    A trim is measured on the file, so it turns with it (``turned_trim``); what was read from the scan is
    forgotten, to be read again the right way up (``_forget_reading``).
    """
    batch, serial, filename = _unarchived_page(output_path, key)
    if serial in batch.slices:
        raise ValueError(f"{key} is a crop; it is turned the way its sheet is. Unslice the sheet to rotate it.")
    if _is_sliced_sheet(output_path, batch, serial, key):
        raise ValueError(f"{key} is sliced; unslice it before rotating it, since its crops are cut upright.")
    rotate_file_upright(input_path / filename, top_points)
    band = load_trims(output_path).get(key)
    if band is not None:
        set_trim(output_path, key, turned_trim(band, top_points))
    _forget_reading(output_path, batch, key)


def trim_page(output_path: Path, key: str, band: Trim | None) -> None:
    """Trim an unarchived page (``None`` keeps all of it). OCR reads only the band from now on, and a
    page it has already read is read again by its next run.

    A sliced sheet is never read (its crops are), so it can't be trimmed; each crop can, like any page.
    """
    batch, serial, _ = _unarchived_page(output_path, key)
    if band is not None and _is_sliced_sheet(output_path, batch, serial, key):
        raise ValueError(f"{key} is sliced into crops, which are what OCR reads: trim a crop instead.")
    set_trim(output_path, key, band)


def page_scan(output_path: Path, input_path: Path, key: str) -> Path:
    """An unarchived page's scan file (KeyError for a page that isn't one, FileNotFoundError for a scan that
    isn't in the input folder)."""
    _, _, filename = _unarchived_page(output_path, key)
    path = input_path / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def straighten_page_image(output_path: Path, input_path: Path, key: str, degrees: float) -> None:
    """Turn an unarchived page's scan ``degrees`` counter-clockwise in place, to level a slight tilt.

    The page's trim, measured on the file, moves with it (``straightened_trim``, keeping all it held); what
    was read from the scan is forgotten, to be read again level (``_forget_reading``).
    """
    batch, serial, filename = _unarchived_page(output_path, key)
    if serial in batch.slices:
        raise ValueError(f"{key} is a crop; it is turned the way its sheet is. Unslice the sheet to straighten it.")
    if _is_sliced_sheet(output_path, batch, serial, key):
        raise ValueError(f"{key} is sliced; unslice it before straightening it, since its crops are cut upright.")
    path = input_path / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    sizes = straighten_file(path, degrees)
    if sizes is None:
        return
    band = load_trims(output_path).get(key)
    if band is not None:
        set_trim(output_path, key, straightened_trim(band, degrees, *sizes))
    _forget_reading(output_path, batch, key)


# =====================================================================================
# OCR
# =====================================================================================
@dataclass
class OcrPlan:
    total: int
    processed: int
    failed: int
    missing_images: int
    items: list[tuple[str, Path]]
    #: pages that would be read but sit in a batch another job holds; the next run picks them up
    waiting: int = 0
    #: pages read successfully, but trimmed differently since, so due to be read again
    retrimmed: int = 0

    @property
    def batches(self) -> frozenset[int]:
        """The batches this run reads, which it holds while it runs."""
        return frozenset(parse_batch_serial_key(key)[0] for key, _ in self.items)


def plan_ocr(output_path: Path, input_path: Path, batch_id: int | None, reprocess: bool, limit: int,
             held: frozenset[int] = frozenset()) -> OcrPlan:
    """Pages to OCR in scope (all unarchived batches, or one). Without ``reprocess`` only pages
    without a successful result; ``limit`` > 0 caps the run. Pages in ``held`` batches (another job is
    working on them) are left out and counted as waiting. A sliced sheet is never read: its crops are."""
    index = _load_index(output_path)
    if index is None:
        return OcrPlan(0, 0, 0, 0, [])
    scoped = [(b, s, fn) for b, s, fn in iter_indexed_files(index, include_archived=False)
              if batch_id is None or b == batch_id]
    results = load_ocr_results(output_path)
    sheets = {k for k, d in load_decisions(output_path).items() if d.sliced}
    scoped = [(b, s, fn) for b, s, fn in scoped if batch_serial_key(b, s) not in sheets]
    trims = load_trims(output_path)
    items: list[tuple[str, Path]] = []
    processed = failed = missing = waiting = retrimmed = 0
    for b, s, fn in scoped:
        key = batch_serial_key(b, s)
        result = results.get(key)
        stale = result is not None and result.succeeded and result.trim != trims.get(key)
        if result is not None:
            processed += result.succeeded
            failed += not result.succeeded
            retrimmed += stale
        path = input_path / fn
        if not path.exists():
            missing += 1
            continue
        if reprocess or result is None or not result.succeeded or stale:
            if b in held:
                waiting += 1
            else:
                items.append((key, path))
    if limit > 0:
        items = items[:limit]
    return OcrPlan(total=len(scoped), processed=processed, failed=failed, missing_images=missing, items=items,
                   waiting=waiting, retrimmed=retrimmed)


def _run_ocr(output_path: Path, items: list[tuple[str, Path]], provider: OcrProvider, structured: bool,
            progress: Progress, model: str, shuffle: bool = True) -> str:
    """OCR each page, saving its batch's file after every image (so an interruption loses at most one page).

    The run holds the batches it reads, so it is the only writer of their files: each is loaded once and
    saved whole, and no other batch's file is ever touched.
    """
    work = list(items)
    if shuffle:
        random.shuffle(work)
    progress.set_total(len(work))
    by_batch: dict[int, dict[str, OcrResult]] = {}
    trims = load_trims(output_path)   # the run holds its batches, so their trims can't change meanwhile
    ran = failed = 0
    for key, path in work:
        if progress.cancelled:
            break
        started = time.perf_counter()
        batch_id = parse_batch_serial_key(key)[0]
        if batch_id not in by_batch:
            by_batch[batch_id] = load_ocr_batch(output_path, batch_id)
        results = by_batch[batch_id]
        band = trims.get(key)
        try:
            with trimmed_file(path, band) as readable:
                markdown = provider.run(readable, structured=False)
                boxes = parse_grounding_output(provider.run(readable, structured=True)) if structured else None
            results[key] = OcrResult(markdown=markdown, boxes=boxes, trim=band)
            ok, error = True, ""
        except Exception as exc:
            results[key] = OcrResult(markdown=traceback.format_exc(), succeeded=False, trim=band)
            ok, error = False, _short_error(exc)
        ran += 1
        failed += 0 if ok else 1
        save_ocr_batch(output_path, batch_id, results)
        _keep_run(output_path, OCR_RUNS, "ocr", key, progress,
                  ModelRun(model=model, at=time.time(), seconds=round(time.perf_counter() - started, 3)),
                  error=error)
        progress.tick(ok, item=key, error=error)
    return _outcome("OCR read", ran, failed, len(work), "page")


def _keep_run(output_path: Path, store: str, kind: str, item: str, progress: Progress, run: ModelRun,
              error: str = "") -> None:
    """Keep what one call took: on the job, beside the result it produced, and in the log of every call.

    A call that failed produced no result, so it goes to the log alone -- but it goes, because that is
    where the answer lives once the job has been replaced by a later one.

    The provider's own payload goes to the job, where it answers "what exactly came back" while the run
    is on screen. What is kept is the counts: an archive should hold what a call cost, not its envelope.
    """
    if error:
        run_log.append(output_path, kind, progress.job_id, item, run, error=error)
        return
    progress.record(run)
    kept = run.model_copy(update={"tokens": run.tokens.model_copy(update={"raw": None})}) if run.tokens else run
    merge_model_runs(output_path, store, {item: kept})
    run_log.append(output_path, kind, progress.job_id, item, kept)


# =====================================================================================
# Parse
# =====================================================================================
@dataclass
class ParsePlan:
    total: int
    processed: int
    tossed: int
    documents: list[DocumentKey]
    index: DocumentIndex = field(repr=False)
    ocr_results: dict[str, OcrResult] = field(repr=False)
    #: documents that would be parsed but sit in a batch another job holds
    waiting: int = 0

    @property
    def batches(self) -> frozenset[int]:
        """The batches this run extracts, which it holds while it runs."""
        return frozenset(doc.batch_id for doc in self.documents)


def plan_parse(output_path: Path, reprocess: bool, limit: int, held: frozenset[int] = frozenset()) -> ParsePlan:
    """Documents whose pages all have OCR and that aren't tossed: without ``reprocess`` only those
    without an extraction. Documents in ``held`` batches (another job is working on them) are left
    out and counted as waiting."""
    index_file = _load_index(output_path)
    indexed_keys = ({batch_serial_key(b, s) for b, s, _ in iter_indexed_files(index_file, include_archived=False)}
                    if index_file else set())
    ocr = {k: r for k, r in load_ocr_results(output_path).items() if r.succeeded and k in indexed_keys}
    ocr_text = {k: r.markdown for k, r in ocr.items()}
    index = build_document_index(output_path, indexed_keys, ocr_keys=set(ocr_text))
    with_ocr = index.doc_keys_with_ocr(ocr_text)
    decisions = load_decisions(output_path)
    eligible = [dk for dk in with_ocr if not (decisions.get(str(dk)) and decisions[str(dk)].verdict == "tossed")]
    extractions = load_extractions(output_path)
    processed = sum(1 for dk in eligible if str(dk) in extractions)
    wanted = eligible if reprocess else [dk for dk in eligible if str(dk) not in extractions]
    documents = [dk for dk in wanted if dk.batch_id not in held]
    if limit > 0:
        documents = documents[:limit]
    return ParsePlan(total=len(with_ocr), processed=processed, tossed=len(with_ocr) - len(eligible),
                     documents=documents, index=index, ocr_results=ocr, waiting=len(wanted) - len(documents))


def _run_parse(output_path: Path, plan: ParsePlan, extract: ExtractFn, custom_instruction: str, progress: Progress,
              model: str, workers: int = 1, shuffle: bool = True, save_every: float = 15.0,
              clock: Callable[[], float] = time.monotonic) -> str:
    """Extract each document, saving every ``save_every`` seconds and at the end (also on errors).

    A save writes only what THIS run extracted, merged into the file as it is at that moment. Other
    things change ``extractions.json`` while a long Parse runs — regrouping another batch clears that
    batch's extractions — and writing back a copy loaded when the run started would quietly undo them.

    ``workers`` documents are extracted at once. A model on this machine takes one, having one set of
    weights to work with; a hosted one takes as many as its rate limit leaves room for, which
    ``rate_budget`` reads off each response and every worker waits on together.

    A refusal that asks for longer than a run should wait -- a day's quota rather than a minute's --
    ends the run instead: the documents left are untouched, and running Parse again later takes them.
    """
    work = list(plan.documents)
    if shuffle:
        random.shuffle(work)
    progress.set_total(len(work))
    extractions: dict[str, DocumentExtraction] = {}       # this run's results only
    counts = {"ran": 0, "failed": 0, "last_save": clock()}
    guard = threading.Lock()
    stopped: list[str] = []               # why the run gave up, if it did

    def parse_one(doc_key) -> None:
        if progress.cancelled or stopped:
            return
        ocr_text, has_boxes = plan.index.concat_ocr_with_boxes(doc_key, plan.ocr_results)
        used: list[TokenUse] = []
        started = time.perf_counter()
        try:
            result = _extract_when_allowed(extract, ocr_text, has_boxes, custom_instruction, used, progress)
            ok, error = True, ""
        except Cancelled:
            return                                # cancelled while queueing: not attempted, so not counted
        except TooLongToWait as exc:
            with guard:
                stopped.append(str(exc))          # and every document still queued goes untouched
            progress.say(str(exc))
            return
        except Exception as exc:  # the previous extraction (if any) is kept
            result, ok, error = None, False, _short_error(exc)
        tokens = used[0] if used else None
        _keep_run(output_path, EXTRACTION_RUNS, "parse", str(doc_key), progress,
                  ModelRun(model=model, at=time.time(), seconds=round(time.perf_counter() - started, 3),
                           tokens=tokens, cost=cost_of(model, tokens) if tokens else None),
                  error=error)
        with guard:
            if result is not None:
                extractions[str(doc_key)] = result
            counts["ran"] += 1
            counts["failed"] += 0 if ok else 1
            due = clock() - counts["last_save"] > save_every
            if due:
                counts["last_save"] = clock()
                produced = dict(extractions)
        progress.tick(ok, item=str(doc_key), error=error)
        if due:
            merge_extractions(output_path, produced)

    try:
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="parse") as pool:
                for future in [pool.submit(parse_one, doc_key) for doc_key in work]:
                    future.result()
        else:
            for doc_key in work:
                parse_one(doc_key)
    finally:
        merge_extractions(output_path, extractions)
    outcome = _outcome("Parsed", counts["ran"], counts["failed"], len(work), "document")
    return f"{outcome} {stopped[0]}" if stopped else outcome


#: How many times a document refused for pacing is offered again before it counts as failed.
RATE_LIMIT_TRIES = 3


class Cancelled(Exception):
    """The run was cancelled while this document was waiting its turn."""


class TooLongToWait(Exception):
    """The model asked for a wait no run should sit through, so the run stops instead."""


def _extract_when_allowed(extract: ExtractFn, ocr_text: str, has_boxes: bool, custom_instruction: str,
                          used: list[TokenUse], progress: Progress) -> DocumentExtraction:
    """Extract, taking a place among the calls in flight and waiting for room in the rate limit.

    Being refused is pacing, not a bad document, so the document comes round again rather than failing
    with the rest. Every worker shares one budget, so a refusal narrows the whole run at once.
    """
    for attempt in range(RATE_LIMIT_TRIES):
        _sleep_while_running(extraction.budget.wait_for(), progress)
        while not extraction.budget.acquire():
            if progress.cancelled:
                raise Cancelled
        try:
            result = call_extractor(extract, ocr_text, has_boxes, custom_instruction, on_usage=used.append)
        except RateLimitError as exc:
            extraction.budget.release(ok=False)
            asked = _retry_after(exc)
            if extraction.budget.too_long_to_wait(asked):
                raise TooLongToWait(
                    f"The model asked for {round(asked / 60)} minutes before the next call, which is a "
                    f"quota rather than a busy minute: stopping. The documents left are untouched; run "
                    f"Parse again once it comes round.") from exc
            if attempt == RATE_LIMIT_TRIES - 1 or progress.cancelled:
                raise
            extraction.budget.rate_limited(asked)
            progress.say(f"Rate limited: waiting, and going on with {extraction.budget.slots} at a time.")
        except Exception:
            extraction.budget.release(ok=False)
            raise
        else:
            extraction.budget.release(ok=True)
            return result
    raise AssertionError("unreachable")


def _retry_after(exc: RateLimitError) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    return duration(headers.get("retry-after-ms")) or duration(headers.get("retry-after", "") + "s")


#: A run holding off for the rate limit still has to answer Cancel, so it waits in slices this long.
WAIT_SLICE = 0.5


def _sleep_while_running(seconds: float, progress: Progress) -> None:
    """Wait, in slices, so Cancel is answered while a run is holding off for the rate limit."""
    while seconds > 0 and not progress.cancelled:
        time.sleep(min(WAIT_SLICE, seconds))
        seconds -= WAIT_SLICE


# =====================================================================================
# Finalize: Fix Rotation, Slice and Group each commit their step at once, for every unarchived batch
# =====================================================================================
#: The steps a page finalizes, and what each is called in its commit.
FINALIZE_STEPS = {"rotation": "Fix Rotation", "slice": "Slice", "group": "Group"}


def _finalize_paths(output_path: Path, step: str) -> tuple[list[int], list[str]]:
    """The unarchived batches, and the files (relative to the root) the step writes for them: what its
    Finalize commits. Only files the step's own edits touch, so a scan dropped into the scan folder and not
    indexed yet, or another step's work in files of its own, is left for its own milestone."""
    index = _load_index(output_path)
    batches = [b for b in index.batches if not b.archived] if index else []
    ids = [b.batch_id for b in batches]
    ocr = [f"{ARCHIVE_DIR}/{OCR_DIR}/{b}.json" for b in ids]
    work = lambda *names: [f"{ARCHIVE_DIR}/{name}" for name in names]   # noqa: E731
    if step == "rotation":     # a turned scan, what was read from it forgotten (_forget_reading), the decisions
        scans = [f"{SCANS_DIR}/{fn}" for b in batches for s, fn in b.files.items() if s not in b.slices]
        return ids, scans + ocr + work(TRIMS, OCR_RUNS, "extractions.json", EXTRACTION_RUNS, ROTATION_DECISIONS,
                                       ROTATION_LOG)
    if step == "slice":        # the crops, the index that names them, and the results that named others
        return ids, [f"{SCANS_DIR}/{slicing.SLICES_DIR}", *work("batches.json"), *ocr,
                     *work("decisions.json", "extractions.json", OCR_RUNS, EXTRACTION_RUNS, TRIMS)]
    if step == "group":        # the grouping and what it cleared, tosses, trims
        return ids, work("documents.json", "decisions.json", "extractions.json", TRIMS)
    raise KeyError(f"no step {step!r} to finalize")


def finalize_pending(output_path: Path, step: str) -> tuple[list[int], int | None]:
    """The unarchived batches, and how many of the step's files differ from the last commit (None before
    the folder has a history: its first Finalize starts it)."""
    ids, paths = _finalize_paths(output_path, step)
    changed = archive_history.changed_paths(root_of(output_path), paths)
    return ids, None if changed is None else len(changed)


def finalize(output_path: Path, step: str) -> str | None:
    """Commit the step's files for every unarchived batch; the commit's short id, or None when nothing had
    changed."""
    ids, paths = _finalize_paths(output_path, step)
    return archive_history.commit(root_of(output_path), f"{FINALIZE_STEPS[step]}: {_batches(ids)}"
                                  if ids else f"{FINALIZE_STEPS[step]}", paths)


# =====================================================================================
# The jobs' milestones: a run's results are committed however it ended
# =====================================================================================
def _commit_run(output_path: Path, what: str, paths: list[str], run: Callable[[], str]) -> str:
    """Run a job's function and commit ``paths``, whether it returned, was cancelled or raised: what a
    run wrote before it stopped is a result too. The commit message carries the run's own summary."""
    try:
        message = run()
    except BaseException as exc:
        try:
            archive_history.commit(root_of(output_path), f"{what} (stopped: {_short_error(exc)})", paths)
        except archive_history.HistoryError:
            pass                          # the run's own error is the one to report
        raise
    try:
        archive_history.commit(root_of(output_path), f"{what}: {message}", paths)
    except archive_history.HistoryError as exc:     # the results are saved all the same: the run did its work
        return f"{message} Not committed to the folder's history: {exc}"
    return message


def run_ocr(output_path: Path, items: list[tuple[str, Path]], provider: OcrProvider, structured: bool,
            progress: Progress, model: str, shuffle: bool = True) -> str:
    """``_run_ocr``, then the milestone: the batches' OCR files and the record of the runs."""
    batches = sorted({parsed[0] for key, _ in items if (parsed := parse_batch_serial_key(key))})
    paths = ([f"{ARCHIVE_DIR}/{OCR_DIR}/{b}.json" for b in batches]
             + [f"{ARCHIVE_DIR}/{OCR_RUNS}", f"{ARCHIVE_DIR}/{run_log.FILE}"])
    return _commit_run(output_path, f"OCR with {model}", paths,
                       lambda: _run_ocr(output_path, items, provider, structured, progress, model, shuffle))


def run_parse(output_path: Path, plan: ParsePlan, extract: ExtractFn, custom_instruction: str, progress: Progress,
              model: str, workers: int = 1, shuffle: bool = True, save_every: float = 15.0,
              clock: Callable[[], float] = time.monotonic) -> str:
    """``_run_parse``, then the milestone: the extractions and the record of the runs."""
    paths = [f"{ARCHIVE_DIR}/extractions.json", f"{ARCHIVE_DIR}/{EXTRACTION_RUNS}", f"{ARCHIVE_DIR}/{run_log.FILE}"]
    return _commit_run(output_path, f"Parse with {model}", paths,
                       lambda: _run_parse(output_path, plan, extract, custom_instruction, progress, model, workers,
                                          shuffle, save_every, clock))


# =====================================================================================
# Archive
# =====================================================================================
#: The mid-ingest working files Archive deletes once every file is archived (``ocr/`` is a folder).
CLEANUP_ARTIFACTS = (OCR_DIR, MIGRATED_OCR, "extractions.json", "decisions.json", OCR_RUNS, EXTRACTION_RUNS,
                     TRIMS, ROTATION_DECISIONS)


@dataclass
class ArchiveMove:
    key: str
    filename: str
    destination: str
    note: str = ""


@dataclass
class ArchivePlan:
    unarchived_batches: int
    complete_batches: int
    documents: int
    multipage: int
    files: int
    accepted: int
    marked: int
    tossed: int
    moves: list[ArchiveMove]
    blocker: str | None
    #: scans the archive already holds that are still in the scan folder (earlier runs' too): removed once
    #: the archive is committed. Known only when the plan was given the scan folder.
    scans_to_remove: int = 0
    # what run_archive needs, not shown
    complete_batch_ids: list[int] = field(default_factory=list, repr=False)
    doc_keys: list[str] = field(default_factory=list, repr=False)
    decisions: dict[str, ReviewDecision] = field(default_factory=dict, repr=False)
    index: DocumentIndex | None = field(default=None, repr=False)


def archived_scans_left(output_path: Path, input_path: Path, index: ScanIndex | None = None) -> list[str]:
    """Scans still in the scan folder that the archive holds a page of (image and sidecar), and no batch
    still being ingested needs: what Archive removes once the archive is committed."""
    index = index if index is not None else _load_index(output_path)
    if index is None or not input_path.is_dir():
        return []
    needed = {fn for b in index.batches if not b.archived for fn in b.files.values()}
    archived = {fn for b in index.batches if b.archived for fn in b.files.values()}
    disposable = (archived & set(filed_scan_pages(output_path))) - needed
    return sorted(fn for fn in disposable if (input_path / fn).is_file())


def _page_place(index: DocumentIndex, key: str) -> int:
    """A page's place in its document, from 1 (the grouped order, which can differ from scan order)."""
    return index.keys_for_doc(index.key_to_doc_key(key)).index(key) + 1


def _page_sort(index: DocumentIndex, key: str) -> tuple[int, int, int]:
    """Documents in scan order, each one's pages together and in page order."""
    doc = index.key_to_doc_key(key)
    return doc.batch_id, doc.first_serial, _page_place(index, key)


def _archive_scope(output_path: Path, scan_index: ScanIndex):
    """What Archive works on: every unarchived page's filename by key, the decisions, the document index,
    the unarchived batches, those whose every document is decided, and those batches' documents."""
    indexed = iter_indexed_files(scan_index, include_archived=False)
    key_to_filename = {batch_serial_key(b, s): fn for b, s, fn in indexed}
    all_decisions = load_decisions(output_path)
    index = build_document_index(output_path, set(key_to_filename))
    unarchived = [b for b in scan_index.batches if not b.archived]
    complete = [b for b in unarchived
                if all(str(index.key_to_doc_key(batch_serial_key(b.batch_id, s))) in all_decisions for s in b.files)]
    doc_keys = sorted({str(index.key_to_doc_key(batch_serial_key(b.batch_id, s))) for b in complete for s in b.files})
    return key_to_filename, all_decisions, index, unarchived, complete, doc_keys


def archive_ready_documents(output_path: Path) -> int:
    """How many documents Archive would file: those of the fully reviewed batches, and only when
    ``plan_archive`` has no blocker (every unarchived batch reviewed, no interrupted slice). Cheaper than
    the plan: nothing in the archive is read."""
    scan_index = _load_index(output_path)
    if scan_index is None:
        return 0
    _, all_decisions, _, unarchived, complete, doc_keys = _archive_scope(output_path, scan_index)
    if not unarchived or len(complete) != len(unarchived):
        return 0
    if any(slicing.mismatches(b, all_decisions) for b in unarchived):
        return 0
    return len(doc_keys)


def plan_archive(output_path: Path, input_path: Path | None = None) -> ArchivePlan:
    """Where every file of the fully reviewed batches would go, for the page to preview; given the scan
    folder, also how many scans already filed would leave it."""
    scan_index = _load_index(output_path)
    if scan_index is None:
        return ArchivePlan(0, 0, 0, 0, 0, 0, 0, 0, [], "Run File Index first to create batches.json.")
    scans_to_remove = len(archived_scans_left(output_path, input_path, scan_index)) if input_path else 0
    key_to_filename, all_decisions, index, unarchived, complete, doc_keys = _archive_scope(output_path, scan_index)
    organized = scan_organized_filenames(output_path)
    decisions = {dk: all_decisions[dk] for dk in doc_keys if dk in all_decisions}

    def pages_of(doc_key: str) -> list[str]:
        return index.keys_for_doc(DocumentKey.parse(doc_key) or DocumentKey.from_group([doc_key]))

    def pending(keys) -> dict:
        return {k: v for k, v in keys.items() if k in key_to_filename and key_to_filename[k] not in organized}

    by_doc = {DocumentKey.parse(k): v for k, v in decisions.items() if DocumentKey.parse(k)}
    files = pending(index.expand_decisions(by_doc))
    accepted = {DocumentKey.parse(k): v for k, v in decisions.items() if v.verdict == "accepted" and DocumentKey.parse(k)}
    accepted_files = pending(index.expand_decisions(accepted))
    marked_docs = [k for k, v in decisions.items() if v.verdict == "marked"]
    tossed_docs = [k for k, v in decisions.items() if v.verdict not in ("accepted", "marked")]

    blocker = None
    all_complete = bool(unarchived) and len(complete) == len(unarchived)
    slice_problems = [p for b in unarchived for p in slicing.mismatches(b, all_decisions)]
    if not unarchived:
        # with only scans to clear out, a run commits the archive and removes them
        blocker = None if scans_to_remove else "No new files to organize."
    elif slice_problems:   # an interrupted slice: saving or unslicing the sheet repairs it
        blocker = f"Fix the sliced sheets on the Slice page first: {'; '.join(slice_problems)}."
    elif not all_complete:
        blocker = "Review all files before archiving."

    moves: list[ArchiveMove] = []
    if all_complete:
        destinations = plan_accepted_destinations(
            accepted_files, scan_existing_names(output_path), key_to_filename=key_to_filename,
            key_to_sort={k: _page_sort(index, k) for k in accepted_files},
        )
        for folder, docs in (("marked", marked_docs), ("tossed", tossed_docs)):
            for doc_key in docs:
                for key in pages_of(doc_key):
                    if key in key_to_filename and key_to_filename[key] not in organized:
                        # a crop's name includes its slices/ folder; the scan's own name is enough here
                        destinations[key] = f"{folder}/{Path(key_to_filename[key]).name}"
        crop_of = {batch_serial_key(b.batch_id, s): batch_serial_key(b.batch_id, of.sheet)
                   for b in unarchived for s, of in b.slices.items()}

        def note(key: str) -> str:
            if key in crop_of:
                return f"crop of {crop_of[key]}"
            return "sliced sheet" if key in all_decisions and all_decisions[key].sliced else ""

        moves = [ArchiveMove(key=k, filename=key_to_filename[k], destination=d, note=note(k))
                 for k, d in sorted(destinations.items(), key=lambda kv: (parse_batch_serial_key(kv[0]) or (0, 0), kv[0]))]

    return ArchivePlan(
        unarchived_batches=len(unarchived), complete_batches=len(complete), documents=len(doc_keys),
        multipage=sum(1 for dk in doc_keys if len(pages_of(dk)) > 1), files=len(files),
        accepted=len(accepted_files),
        marked=sum(len(pages_of(dk)) for dk in marked_docs), tossed=sum(len(pages_of(dk)) for dk in tossed_docs),
        moves=moves, blocker=blocker, scans_to_remove=scans_to_remove,
        complete_batch_ids=[b.batch_id for b in complete], doc_keys=doc_keys, decisions=decisions, index=index,
    )


class ArchiveIncomplete(Exception):
    """Some files could not be archived; nothing was finalized."""


def _archive_commit_message(batch_ids: list[int], files: int) -> str:
    return f"Archive {_batches(batch_ids)}: {files} file{'s' if files != 1 else ''}"


def _page_paths(root: Path, pages: Iterable[Path]) -> list[str]:
    """A page's image and sidecar, relative to the root, for a pathspec."""
    return [p.relative_to(root).as_posix() for page in pages for p in (page, sidecar_path_for(page))]


def run_archive(output_path: Path, input_path: Path, progress: Progress) -> str:
    """Copy every file to its destination with a sidecar; then, only if all succeeded, update the smart-
    match cache, mark the batches archived and delete the mid-ingest files. Then commit what it filed to the
    folder's history (and any page filed before the history began, or before a commit that failed), and
    remove every scan whose filed page is in the last commit from the scan folder, committing that too.
    Re-running resumes: files already archived are skipped by the plan, and a run with nothing new to file
    still clears the scans out."""
    plan = plan_archive(output_path, input_path)
    if plan.blocker:
        raise ValueError(plan.blocker)
    assert plan.index is not None
    ocr = {k: r for k, r in load_ocr_results(output_path).items() if r.succeeded}
    extractions = load_extractions(output_path)
    ocr_runs = load_model_runs(output_path, OCR_RUNS)
    rotation = load_rotation_decisions(output_path)
    extraction_runs = load_model_runs(output_path, EXTRACTION_RUNS)
    crops = {(b.batch_id, c.serial): (batch_serial_key(b.batch_id, c.of.sheet), [c.of.row, c.of.col], c.box)
             for b in load_scan_index(output_path).batches if not b.archived for c in slicing.current_crops(b)}
    trims = load_trims(output_path)
    progress.set_total(len(plan.moves))

    copied = 0
    for move in plan.moves:
        if progress.cancelled:
            return (f"Cancelled after {copied} of {len(plan.moves)} file(s); nothing was finalized. "
                    f"Run Archive again to continue.")
        source = input_path / move.filename
        target = output_path / move.destination
        placed = False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copy under a temporary name first: a file only counts as archived once it is complete
            # and has its sidecar, so a failed step leaves nothing that a re-run would skip.
            partial = target.with_name(target.name + ".partial")
            shutil.copy(str(source), str(partial))
            partial.replace(target)
            placed = True
            doc = plan.index.key_to_doc_key(move.key)
            parsed = parse_batch_serial_key(move.key)
            of = crops.get(parsed) if parsed else None
            write_sidecar(target, Sidecar(
                original_filename=move.filename,
                batch_id=parsed[0] if parsed else None,
                serial=parsed[1] if parsed else None,
                review=plan.decisions[str(doc)],
                document_key=str(doc) if doc.is_multi_page else None,
                page=_page_place(plan.index, move.key),
                ocr=ocr.get(move.key),
                extraction=extractions.get(str(doc)),
                ocr_run=ocr_runs.get(move.key),
                extraction_run=extraction_runs.get(str(doc)),
                slice_of=of[0] if of else None,
                slice_cell=of[1] if of else None,
                slice_box=of[2] if of else None,
                trim=trims.get(move.key), rotation=rotation.get(move.key),
            ))
            copied += 1
            progress.tick(True, item=move.filename)
        except Exception as exc:
            target.with_name(target.name + ".partial").unlink(missing_ok=True)
            if placed:  # the image made it but its sidecar didn't: undo, so the next run redoes both
                target.unlink(missing_ok=True)
                delete_sidecar(target)
            progress.tick(False, item=move.filename, error=_short_error(exc))

    if copied < len(plan.moves):
        raise ArchiveIncomplete(f"{len(plan.moves) - copied} file(s) could not be archived; batches were not marked "
                                f"archived and no files were cleaned up. Fix the errors and run Archive again.")

    cleaned: list[str] = []
    if plan.moves:
        cache = load_smart_match_cache(output_path)
        for doc_key in plan.doc_keys:
            extraction = extractions.get(doc_key)
            if isinstance(extraction, ReceiptResult):
                extracted, phone = extraction.name, extraction.phone
            elif isinstance(extraction, OtherResult):
                extracted, phone = extraction.title, ""
            else:
                extracted, phone = "", ""
            cache[doc_key] = {"extracted": extracted, "confirmed": plan.decisions[doc_key].name,
                              "extracted_phone": phone}
        save_smart_match_cache(output_path, cache)

        scan_index = load_scan_index(output_path)
        for batch in scan_index.batches:
            if batch.batch_id in plan.complete_batch_ids:
                batch.archived = True
        save_scan_index(output_path, scan_index)

        cleaned = [name for name in CLEANUP_ARTIFACTS if (output_path / name).exists()]
        for name in cleaned:
            if (output_path / name).is_dir():
                shutil.rmtree(output_path / name)
            else:
                (output_path / name).unlink()

    # Milestone: what this run filed. A HistoryError (git missing, a repository problem) fails the run here,
    # with the batches filed and every scan still where it was; the next run commits and clears them out.
    root = root_of(output_path)
    sha = None
    if plan.moves:
        progress.say("Committing the filed pages…")
        sha = archive_history.commit(root, _archive_commit_message(plan.complete_batch_ids, copied), [
            *_page_paths(root, (output_path / m.destination for m in plan.moves)),
            f"{ARCHIVE_DIR}/batches.json", f"{ARCHIVE_DIR}/smart_match_cache.json", f"{ARCHIVE_DIR}/{ROTATION_LOG}",
            f"{ARCHIVE_DIR}/{WORKSHOP_LOG}",
            *(f"{ARCHIVE_DIR}/{name}" for name in CLEANUP_ARTIFACTS)])

    # A filed page the history doesn't have yet is Archive's too: filed before the history began, or by a
    # run whose commit failed. (One the history has but that changed since is someone's edit, and waits.)
    pages = filed_scan_pages(output_path)
    tracked = archive_history.tracked_paths(root, ARCHIVE_DIR)
    earlier = [page for page in pages.values() if page.relative_to(root).as_posix() not in tracked]
    earlier_sha = None
    if earlier:
        progress.say(f"Committing {len(earlier)} page(s) filed earlier…")
        earlier_sha = archive_history.commit(
            root, f"Archive: {len(earlier)} page{'s' if len(earlier) != 1 else ''} filed before the history",
            _page_paths(root, earlier))
        tracked = archive_history.tracked_paths(root, ARCHIVE_DIR)

    # A scan leaves the scan folder only for a filed page that is in the last commit as it is on disk; and
    # its own bytes go into the history first, since the page may not be them any more (the Workshop turns
    # a page in place) and a scan filed before the history began was never committed at File Index.
    progress.say("Removing the filed scans from the scan folder…")
    dirty = archive_history.changed_paths(root, [ARCHIVE_DIR]) or set()

    def committed(page: Path) -> bool:
        image, sidecar = _page_paths(root, [page])
        return image in tracked and image not in dirty and sidecar not in dirty

    left = archived_scans_left(output_path, input_path)
    leaving = [fn for fn in left if committed(pages[fn])]
    waiting = len(left) - len(leaving)
    if leaving:
        archive_history.commit(root, f"Archive: {len(leaving)} scan{'s' if len(leaving) != 1 else ''} kept as "
                                     f"scanned, before they leave the scan folder",
                               [f"{SCANS_DIR}/{fn}" for fn in leaving])
    kept = archive_history.tracked_paths(root, SCANS_DIR)
    unkept = archive_history.changed_paths(root, [SCANS_DIR]) or set()

    removed, in_use = [], []
    for filename in leaving:
        if f"{SCANS_DIR}/{filename}" not in kept or f"{SCANS_DIR}/{filename}" in unkept:
            waiting += 1                      # changed while this ran: its bytes aren't the committed ones
            continue
        try:
            (input_path / filename).unlink()
            removed.append(filename)
        except OSError:              # open in a viewer, say: still filed, so it goes next time
            in_use.append(filename)
    if removed:
        archive_history.commit(root, f"Archive: {len(removed)} scan{'s' if len(removed) != 1 else ''} cleared "
                                     f"out of the scan folder", [f"{SCANS_DIR}/{fn}" for fn in removed])

    parts = [f"Archived {copied} file(s)." if copied else "Nothing new to file."]
    if cleaned:
        parts.append(f"Cleaned up {', '.join(cleaned)}.")
    if sha:
        parts.append(f"Committed the filed pages ({sha}).")
    if earlier_sha:
        parts.append(f"Committed {len(earlier)} page(s) filed earlier ({earlier_sha}).")
    if removed:
        parts.append(f"Removed {len(removed)} scan(s) from the scan folder.")
    if waiting:
        parts.append(f"{waiting} scan(s) stay until their filed pages are committed (edited since; a manual "
                     f"commit does it).")
    if in_use:
        parts.append(f"{len(in_use)} scan(s) couldn't be removed (in use?): {', '.join(in_use[:5])}"
                     f"{'…' if len(in_use) > 5 else ''}; the next run will.")
    return " ".join(parts)
