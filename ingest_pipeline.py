"""The ingest pipeline steps — File Index, OCR, Parse, Archive — without Streamlit.

Ports what ``pages/ingest/{file_index,ocr,parse,archive}.py`` do into plain functions: each step has a
``plan_*`` (what would happen, for the page to show) and, for the long steps, a ``run_*`` that takes a
progress context and the model to use as arguments, so tests run them with fakes (no GPU, no network).

Deliberate fixes over the Streamlit pages:
- OCR and Parse never drop results for items outside the current run (the pages rewrote ocr.json /
  extractions.json from a filtered or emptied dict).
- Reprocessing replaces results as each item finishes, so cancelling keeps the old results of items
  not yet redone.
- OCR runs the second "structured" pass only for providers that return grounding boxes.
- Parse failures are reported per item; Archive reports per-file errors and only finalizes (marks
  batches archived, deletes the mid-ingest files) when every file was archived.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from data import (
    build_document_index,
    clear_extractions_decisions_for_batch,
    delete_sidecar,
    load_decisions,
    load_document_groups,
    load_extractions,
    load_ocr_results,
    load_smart_match_cache,
    replace_groups_for_batch,
    save_decisions,
    save_extractions,
    save_ocr_results,
    save_scan_index,
    save_smart_match_cache,
    scan_organized_filenames,
    write_sidecar,
)
from document_grouping import build_display_state, rotate_upright, split_groups_at_tossed_boundaries
from grounding import parse_grounding_output
from indexing_schemes import SCHEMES, parse_canon_filename
from models import (
    DocumentExtraction,
    DocumentIndex,
    DocumentKey,
    OcrResult,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    ScanBatch,
    ScanIndex,
    Sidecar,
    batch_serial_key,
    filename_to_batch_serial,
    iter_indexed_files,
    load_scan_index,
    parse_batch_serial_key,
)
from organize_utils import plan_accepted_destinations, scan_existing_names
from settings import IMAGE_EXTENSIONS


class Progress(Protocol):
    """What the long steps report to (api.jobs.JobContext in the web app)."""

    @property
    def cancelled(self) -> bool: ...
    def set_total(self, total: int) -> None: ...
    def tick(self, ok: bool = True, item: str = "", error: str = "") -> None: ...
    def say(self, message: str) -> None: ...


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
    return proposal.batches


# =====================================================================================
# Document grouping (File Index)
# =====================================================================================
@dataclass
class GroupingPage:
    key: str
    serial: int
    filename: str
    tossed: bool


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
    pages = [GroupingPage(key=k, serial=s, filename=batch.files[s], tossed=k in tossed)
             for k, s in zip(keys, sorted(batch.files))]
    return GroupingState(batch=batch, pages=pages, display_keys=display_keys, active_links=links,
                         saved_groups=_active_saved_groups(saved, keys, tossed))


def save_grouping(output_path: Path, batch_id: int, groups: list[list[str]]) -> bool:
    """Save a batch's page grouping (groups of active pages). Returns False (and changes nothing) if the
    multi-page groups are unchanged. A change clears the batch's extractions and review decisions, as in
    Streamlit, because documents' keys and contents changed — except tosses: a tossed document keeps its
    decision and, if it was a saved group, its group (tossed pages can't be regrouped)."""
    _, batch = _non_archived_batch(output_path, batch_id)
    keys = _batch_keys(batch)
    tossed = _tossed_keys(output_path, keys, load_decisions(output_path))
    active = set(keys) - tossed
    seen: set[str] = set()
    for group in groups:
        for key in group:
            if key not in active:
                raise ValueError(f"{key} is not an active page of batch {batch_id}")
            if key in seen:
                raise ValueError(f"{key} appears in more than one group")
            seen.add(key)
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
    if tossed:
        decisions[doc_key] = ReviewDecision(verdict="tossed", document_type="corrupted", name="", date="", time="",
                                            cost=0.0, currency="")
    elif doc_key in decisions:
        del decisions[doc_key]
    else:
        return
    save_decisions(output_path, decisions)


def rotate_page_image(output_path: Path, input_path: Path, key: str, top_points: str) -> None:
    """Rotate an unarchived page's scan in place (like Streamlit's ←/→/↓ buttons)."""
    from PIL import Image

    parsed = parse_batch_serial_key(key)
    if parsed is None:
        raise KeyError(f"not a page key: {key}")
    _, batch = _non_archived_batch(output_path, parsed[0])
    filename = batch.files.get(parsed[1])
    if filename is None:
        raise KeyError(f"no page {key}")
    path = input_path / filename
    with Image.open(path) as img:
        image_format = img.format
        upright = rotate_upright(img.convert("RGB"), top_points)
    # The scan is the only copy: write a sibling file and swap it in, so a crash can't truncate it.
    tmp = path.with_name(f"{path.stem}.rotating{path.suffix}")
    try:
        upright.save(tmp, format=image_format, **({"quality": 95} if image_format == "JPEG" else {}))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


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


def plan_ocr(output_path: Path, input_path: Path, batch_id: int | None, reprocess: bool, limit: int) -> OcrPlan:
    """Pages to OCR in scope (all unarchived batches, or one). Without ``reprocess`` only pages
    without a successful result; ``limit`` > 0 caps the run."""
    index = _load_index(output_path)
    if index is None:
        return OcrPlan(0, 0, 0, 0, [])
    scoped = [(b, s, fn) for b, s, fn in iter_indexed_files(index, include_archived=False)
              if batch_id is None or b == batch_id]
    results = load_ocr_results(output_path)
    items: list[tuple[str, Path]] = []
    processed = failed = missing = 0
    for b, s, fn in scoped:
        key = batch_serial_key(b, s)
        result = results.get(key)
        if result is not None:
            processed += result.succeeded
            failed += not result.succeeded
        path = input_path / fn
        if not path.exists():
            missing += 1
            continue
        if reprocess or result is None or not result.succeeded:
            items.append((key, path))
    if limit > 0:
        items = items[:limit]
    return OcrPlan(total=len(scoped), processed=processed, failed=failed, missing_images=missing, items=items)


def run_ocr(output_path: Path, items: list[tuple[str, Path]], provider: OcrProvider, structured: bool,
            progress: Progress, shuffle: bool = True) -> str:
    """OCR each page, saving after every image (so an interruption loses at most one page)."""
    work = list(items)
    if shuffle:
        random.shuffle(work)
    progress.set_total(len(work))
    results = load_ocr_results(output_path)
    ran = failed = 0
    for key, path in work:
        if progress.cancelled:
            break
        try:
            markdown = provider.run(path, structured=False)
            boxes = parse_grounding_output(provider.run(path, structured=True)) if structured else None
            results[key] = OcrResult(markdown=markdown, boxes=boxes)
            ok, error = True, ""
        except Exception as exc:
            results[key] = OcrResult(markdown=traceback.format_exc(), succeeded=False)
            ok, error = False, _short_error(exc)
        ran += 1
        failed += 0 if ok else 1
        save_ocr_results(output_path, results)
        progress.tick(ok, item=key, error=error)
    return _outcome("OCR read", ran, failed, len(work), "page")


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


def plan_parse(output_path: Path, reprocess: bool, limit: int) -> ParsePlan:
    """Documents whose pages all have OCR and that aren't tossed: without ``reprocess`` only those
    without an extraction."""
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
    documents = eligible if reprocess else [dk for dk in eligible if str(dk) not in extractions]
    if limit > 0:
        documents = documents[:limit]
    return ParsePlan(total=len(with_ocr), processed=processed, tossed=len(with_ocr) - len(eligible),
                     documents=documents, index=index, ocr_results=ocr)


def run_parse(output_path: Path, plan: ParsePlan, extract: ExtractFn, custom_instruction: str, progress: Progress,
              shuffle: bool = True, save_every: float = 15.0, clock: Callable[[], float] = time.monotonic) -> str:
    """Extract each document, saving every ``save_every`` seconds and at the end (also on errors)."""
    work = list(plan.documents)
    if shuffle:
        random.shuffle(work)
    progress.set_total(len(work))
    extractions = load_extractions(output_path)
    ran = failed = 0
    last_save = clock()
    try:
        for doc_key in work:
            if progress.cancelled:
                break
            ocr_text, has_boxes = plan.index.concat_ocr_with_boxes(doc_key, plan.ocr_results)
            try:
                extractions[str(doc_key)] = extract(ocr_text, has_boxes=has_boxes, custom_instruction=custom_instruction)
                ok, error = True, ""
            except Exception as exc:  # the previous extraction (if any) is kept
                ok, error = False, _short_error(exc)
            ran += 1
            failed += 0 if ok else 1
            progress.tick(ok, item=str(doc_key), error=error)
            if clock() - last_save > save_every:
                save_extractions(output_path, extractions)
                last_save = clock()
    finally:
        save_extractions(output_path, extractions)
    return _outcome("Parsed", ran, failed, len(work), "document")


# =====================================================================================
# Archive
# =====================================================================================
CLEANUP_ARTIFACTS = ("ocr.json", "extractions.json", "decisions.json")


@dataclass
class ArchiveMove:
    key: str
    filename: str
    destination: str


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
    # what run_archive needs, not shown
    complete_batch_ids: list[int] = field(default_factory=list, repr=False)
    doc_keys: list[str] = field(default_factory=list, repr=False)
    decisions: dict[str, ReviewDecision] = field(default_factory=dict, repr=False)
    index: DocumentIndex | None = field(default=None, repr=False)


def plan_archive(output_path: Path) -> ArchivePlan:
    """Where every file of the fully reviewed batches would go (like Streamlit's Archive preview)."""
    scan_index = _load_index(output_path)
    if scan_index is None:
        return ArchivePlan(0, 0, 0, 0, 0, 0, 0, 0, [], "Run File Index first to create batches.json.")
    indexed = iter_indexed_files(scan_index, include_archived=False)
    key_to_filename = {batch_serial_key(b, s): fn for b, s, fn in indexed}
    all_decisions = load_decisions(output_path)
    organized = scan_organized_filenames(output_path)
    index = build_document_index(output_path, set(key_to_filename))

    unarchived = [b for b in scan_index.batches if not b.archived]
    complete = [b for b in unarchived
                if all(str(index.key_to_doc_key(batch_serial_key(b.batch_id, s))) in all_decisions for s in b.files)]
    doc_keys = sorted({str(index.key_to_doc_key(batch_serial_key(b.batch_id, s))) for b in complete for s in b.files})
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
    if not unarchived:
        blocker = "No new files to organize."
    elif not all_complete:
        blocker = "Review all files before archiving."

    moves: list[ArchiveMove] = []
    if all_complete:
        destinations = plan_accepted_destinations(
            accepted_files, scan_existing_names(output_path), key_to_filename=key_to_filename,
            key_to_sort={k: (parse_batch_serial_key(k) or (0, 0)) for k in accepted_files},
        )
        for folder, docs in (("marked", marked_docs), ("tossed", tossed_docs)):
            for doc_key in docs:
                for key in pages_of(doc_key):
                    if key in key_to_filename and key_to_filename[key] not in organized:
                        destinations[key] = f"{folder}/{key_to_filename[key]}"
        moves = [ArchiveMove(key=k, filename=key_to_filename[k], destination=d)
                 for k, d in sorted(destinations.items(), key=lambda kv: (parse_batch_serial_key(kv[0]) or (0, 0), kv[0]))]

    return ArchivePlan(
        unarchived_batches=len(unarchived), complete_batches=len(complete), documents=len(doc_keys),
        multipage=sum(1 for dk in doc_keys if len(pages_of(dk)) > 1), files=len(files),
        accepted=len(accepted_files),
        marked=sum(len(pages_of(dk)) for dk in marked_docs), tossed=sum(len(pages_of(dk)) for dk in tossed_docs),
        moves=moves, blocker=blocker, complete_batch_ids=[b.batch_id for b in complete], doc_keys=doc_keys,
        decisions=decisions, index=index,
    )


class ArchiveIncomplete(Exception):
    """Some files could not be archived; nothing was finalized."""


def run_archive(output_path: Path, input_path: Path, progress: Progress) -> str:
    """Copy every file to its destination with a sidecar; then, only if all succeeded, update the smart-
    match cache, mark the batches archived and delete the mid-ingest files. Re-running resumes: files
    already archived are skipped by the plan."""
    plan = plan_archive(output_path)
    if plan.blocker:
        raise ValueError(plan.blocker)
    assert plan.index is not None
    ocr = {k: r for k, r in load_ocr_results(output_path).items() if r.succeeded}
    extractions = load_extractions(output_path)
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
            write_sidecar(target, Sidecar(
                original_filename=move.filename,
                batch_id=parsed[0] if parsed else None,
                serial=parsed[1] if parsed else None,
                review=plan.decisions[str(doc)],
                document_key=str(doc) if doc.is_multi_page else None,
                ocr=ocr.get(move.key),
                extraction=extractions.get(str(doc)),
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

    cache = load_smart_match_cache(output_path)
    for doc_key in plan.doc_keys:
        extraction = extractions.get(doc_key)
        if isinstance(extraction, ReceiptResult):
            extracted, phone = extraction.name, extraction.phone
        elif isinstance(extraction, OtherResult):
            extracted, phone = extraction.title, ""
        else:
            extracted, phone = "", ""
        cache[doc_key] = {"extracted": extracted, "confirmed": plan.decisions[doc_key].name, "extracted_phone": phone}
    save_smart_match_cache(output_path, cache)

    scan_index = load_scan_index(output_path)
    for batch in scan_index.batches:
        if batch.batch_id in plan.complete_batch_ids:
            batch.archived = True
    save_scan_index(output_path, scan_index)

    cleaned = [name for name in CLEANUP_ARTIFACTS if (output_path / name).exists()]
    for name in cleaned:
        (output_path / name).unlink()
    return f"Archived {copied} file(s)." + (f" Cleaned up {', '.join(cleaned)}." if cleaned else "")
