"""The Marked Workshop: rescuing documents that review marked instead of accepting or tossing.

A marked document sits in ``marked/`` with one sidecar per page, each carrying the document key it
belongs to. The pages are grouped back into documents, and accepting or tossing moves all of them
together: walking the files one at a time would show a two-page document twice, and accepting one page
would file it alone and strand the other (issue #14).
"""

from __future__ import annotations


import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import review_logic as rl
import rotation_review
from data import load_smart_match_cache, log_workshop_record, read_sidecar, save_smart_match_cache
from dedupe_candidates import WEEK_WINDOW, parse_verdict_datetime
from deskew import straighten_file, straightened_trim
from document_files import MARKED, ensure_movable, load_pages, place_document, toss_document
from document_grouping import rotate_file_upright
from models import (
    DocumentExtraction, DocumentFields, DocumentKey, OcrResult, ReceiptResult, ReviewDecision, RotationPrediction,
    ScanIndex, Sidecar, Trim, WorkshopRecord, WorkshopReread,
    batch_serial_key, ocr_page_section, turned_trim,
)
from scan_enhance import TREATMENTS_VERSION
from viz_records import page_order


@dataclass
class MarkedDocument:
    """One marked document: its pages in scan order, and what review last recorded about it."""

    key: str
    pages: list[Path]
    sidecars: list[Sidecar] = field(default_factory=list)

    @property
    def first(self) -> Sidecar:
        return self.sidecars[0]

    @property
    def filenames(self) -> list[str]:
        return [path.name for path in self.pages]


def _unrecorded(path: Path) -> Sidecar:
    """A scan in ``marked/`` without a sidecar (put there by hand): marked, with nothing read yet."""
    return Sidecar(original_filename=path.name, review=ReviewDecision(
        verdict="marked", document_type="receipt", name="", date="", time="", cost=0.0, currency="", comment=""))


def marked_documents(output_path: Path) -> list[MarkedDocument]:
    """Everything in ``marked/``, grouped into documents and ordered by scan order.

    A scan without a sidecar is listed too, as its own document with nothing read yet; deciding it writes
    the sidecar it lacked.
    """
    folder = output_path / MARKED
    if not folder.is_dir():
        return []

    grouped: dict[str, list[tuple[Path, Sidecar]]] = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() == ".json" or path.name.endswith(".enhanced.png"):
            continue
        sidecar = read_sidecar(path) or _unrecorded(path)
        key = sidecar.document_key or _page_key(sidecar) or path.stem
        grouped.setdefault(key, []).append((path, sidecar))

    documents = []
    for key, pages in grouped.items():
        pages.sort(key=lambda page: page_order(page[1], page[0].name))
        documents.append(MarkedDocument(key=key, pages=[p for p, _ in pages], sidecars=[s for _, s in pages]))
    documents.sort(key=lambda document: page_order(document.first, document.pages[0].name))
    return documents


def find(output_path: Path, key: str) -> MarkedDocument | None:
    return next((document for document in marked_documents(output_path) if document.key == key), None)


def ocr_of(document: MarkedDocument, reread: Reread | None = None) -> str:
    """The document's OCR text, pages joined as Review joins them: the reread's if there is one."""
    results = reread.results if reread else [sidecar.ocr for sidecar in document.sidecars]
    return "\n\n".join(f"--- Page {number} ---\n{result.markdown}"
                        for number, result in enumerate(results, start=1) if result and result.markdown)


def extractor_input(results: list[OcrResult]) -> tuple[str, bool]:
    """What the extractor is given for a reread: every page as the pipeline's Parse gives it, boxes and all."""
    return ("\n\n".join(ocr_page_section(number, result) for number, result in enumerate(results, start=1)),
            any(result.boxes for result in results))


@dataclass(frozen=True)
class Reread:
    """What reading a marked document again produced, held until the document is decided.

    Nothing on disk changes until then: Accept keeps it (turning the files as they were read, so its
    boxes line up), Toss or Discard drops it. It is held in memory, so it doesn't outlive the server.
    """

    pages: tuple[str, ...]           # the files it read, so it is dropped if the document changed since
    results: list[OcrResult]
    extraction: DocumentExtraction
    top_points: str
    ocr_model: str
    extractor: str
    #: How far it straightened the pages after turning them (degrees counter-clockwise).
    degrees: float = 0.0
    #: The treatment it read the pages with, and every one of its settings (scan_enhance.treatment_settings).
    treatment: str = "none"
    settings: dict[str, float] = field(default_factory=dict)


class Rereads:
    """Rereads waiting for a decision, by document key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[str, Reread] = {}

    def put(self, key: str, reread: Reread) -> None:
        with self._lock:
            self._by_key[key] = reread

    def get(self, document: MarkedDocument) -> Reread | None:
        with self._lock:
            reread = self._by_key.get(document.key)
            if reread is not None and reread.pages != tuple(document.filenames):
                del self._by_key[document.key]
                return None
            return reread

    def drop(self, key: str) -> None:
        with self._lock:
            self._by_key.pop(key, None)


rereads = Rereads()


def store_reprocessed(document: MarkedDocument, reread: Reread, output_path: Path | None = None,
                      judge: Callable[[Path], RotationPrediction] | None = None) -> None:
    """Write a reread into the document's own sidecars: each page's OCR, and the extraction on the first.

    OCR read the pages turned by ``top_points`` and straightened by ``degrees``, so its boxes are measured on
    that image: the files are turned and straightened too, or the boxes would sit in the wrong places
    wherever the scan is shown later. Only the geometry is kept; the treatments were only there to help OCR
    read. A trim is measured on the file, so it turns and straightens with it (a straightened reread read
    the whole page, ``reading_trim``).

    With ``judge`` (what the rotation detectors say of a page), each page's rotation decision is kept, as
    Fix Rotation keeps its own: in the page's sidecar and the archive's log (``output_path``,
    rotation_review.workshop_decision).
    """
    from data import write_sidecar

    for index, (path, sidecar) in enumerate(zip(document.pages, document.sidecars)):
        if index >= len(reread.results):
            break
        predicted = judge(path) if judge is not None else None
        before, version = rotation_review.kept(path), path.stat().st_mtime_ns
        rotate_file_upright(path, reread.top_points)
        trim = turned_trim(sidecar.trim, reread.top_points)
        if reread.degrees:
            sizes = straighten_file(path, reread.degrees)
            if sizes is not None:
                trim = straightened_trim(trim, reread.degrees, *sizes)
        result = reread.results[index]
        update: dict = {"ocr": result.model_copy(update={"trim": turned_trim(result.trim, reread.top_points)}),
                        "trim": trim}
        if index == 0:
            update["extraction"] = reread.extraction
        if predicted is not None and output_path is not None:
            decision = rotation_review.workshop_decision(
                output_path, path, sidecar, predicted, reread.top_points or None, reread.degrees, before, version, sidecar.trim)
            if decision is not None:
                update["rotation"] = [*(sidecar.rotation or []), decision]
        write_sidecar(path, sidecar.model_copy(update=update))


def accept(output_path: Path, document: MarkedDocument, decision: ReviewDecision,
           reread: Reread | None = None, judge: Callable[[Path], RotationPrediction] | None = None) -> list[str]:
    """File the document into the archive under ``decision``, keeping what the model read.

    The sidecars keep the model's extraction untouched (with the boxes each field came from), as review
    does: the decision is what was confirmed, the extraction what was read. A pending reread is kept. What
    the Workshop did and how the read went (``workshop_record``) goes on the first page and in the log.
    """
    ensure_movable(document.pages)
    _write_missing_sidecars(document)
    record = workshop_record(document, decision, reread)
    if reread is not None:
        store_reprocessed(document, reread, output_path, judge)
    pages = load_pages(document.pages)
    first = pages[0][1].original_filename
    placed = place_document(output_path, pages, decision, sidecar_for=lambda sidecar: sidecar.model_copy(
        update={"review": decision, **({"workshop": record} if sidecar.original_filename == first else {})}))
    log_workshop_record(output_path, record)
    _remember_name(output_path, pages[0][1], decision)
    rereads.drop(document.key)
    return placed


#: The fields a record compares, and how: a name the same but for case or spacing is the same name (the form
#: offers a known name's usual spelling in place of the one read), a cost the same to the sen.
_SAME = {
    "document_type": lambda a, b: a == b,
    "name": lambda a, b: " ".join(a.split()).casefold() == " ".join(b.split()).casefold(),
    "date": lambda a, b: a == b,
    "time": lambda a, b: a == b,
    "cost": lambda a, b: abs(a - b) < 0.005,
    "currency": lambda a, b: a == b,
}


def workshop_record(document: MarkedDocument, decision: ReviewDecision, reread: Reread | None) -> WorkshopRecord:
    """What the Workshop did to ``document`` and how that read went, for accepting it under ``decision``.

    What was read is the reread's extraction, else the one the document was marked with, else what review
    recorded (a document marked before anything was extracted)."""
    extraction = reread.extraction if reread else document.first.extraction
    read = rl.form_defaults(extraction) if extraction else rl.defaults_from_decision(document.first.review)
    read_fields = DocumentFields(document_type=read.document_type, name=read.name, date=read.date,
                                 time=read.time, cost=read.cost, currency=read.currency)
    accepted = DocumentFields(document_type=decision.document_type, name=decision.name, date=decision.date,
                              time=decision.time, cost=decision.cost, currency=decision.currency)
    return WorkshopRecord(
        at=time.time(), key=document.key, filenames=document.filenames,
        reread=WorkshopReread(
            method=TREATMENTS_VERSION, ocr_model=reread.ocr_model, extractor=reread.extractor,
            top_points=reread.top_points or None, degrees=reread.degrees, treatment=reread.treatment,
            settings=reread.settings, trims=[result.trim for result in reread.results],
        ) if reread else None,
        read=read_fields, accepted=accepted,
        corrected=[name for name, same in _SAME.items()
                   if not same(getattr(read_fields, name), getattr(accepted, name))])


def toss(output_path: Path, document: MarkedDocument) -> list[str]:
    """Move every page of the document into ``tossed/`` (a pending reread goes with it, unkept)."""
    ensure_movable(document.pages)
    _write_missing_sidecars(document)
    tossed = toss_document(output_path, document.pages)
    rereads.drop(document.key)
    return tossed


def reading_trim(band: Trim | None, top_points: str, degrees: float = 0.0) -> Trim | None:
    """The part of a page a reread reads when it is turned from ``top_points`` and straightened ``degrees``:
    its trim, or all of it under a quarter turn or a straightening. A trim runs along the page as stored,
    and Accept, which turns and straightens the file, can't keep it exactly across either; reading the whole
    page keeps the boxes true to it."""
    return None if top_points in ("left", "right") or degrees else band


def marked_page(output_path: Path, filename: str) -> tuple[Path, Sidecar] | None:
    """A page of a marked document, by its file in ``marked/``, with its sidecar."""
    return next(((path, sidecar) for document in marked_documents(output_path)
                 for path, sidecar in zip(document.pages, document.sidecars) if path.name == filename), None)


def trim_page(path: Path, sidecar: Sidecar, band: Trim | None) -> None:
    """Keep only ``band`` of a marked page (None: all of it). The scan is untouched; a reread reads only
    the band, and every display shows only the band."""
    from data import write_sidecar

    write_sidecar(path, sidecar.model_copy(update={"trim": band}))


def _write_missing_sidecars(document: MarkedDocument) -> None:
    """Give a hand-placed scan the sidecar it lacked, so it moves like any other page."""
    from data import write_sidecar

    for path, sidecar in zip(document.pages, document.sidecars):
        if read_sidecar(path) is None:
            write_sidecar(path, sidecar)


def _remember_name(output_path: Path, sidecar: Sidecar, decision: ReviewDecision) -> None:
    """Teach the smart-match cache the name the model read and the name it turned out to be."""
    key = sidecar.document_key or _page_key(sidecar) or sidecar.original_filename
    read = sidecar.extraction
    cache = load_smart_match_cache(output_path)
    cache[key] = {
        "extracted": getattr(read, "name", "") or getattr(read, "title", ""),
        "confirmed": decision.name,
        "extracted_phone": read.phone if isinstance(read, ReceiptResult) else "",
    }
    save_smart_match_cache(output_path, cache)


def _page_key(sidecar: Sidecar) -> str | None:
    if sidecar.batch_id is None or sidecar.serial is None:
        return None
    return batch_serial_key(sidecar.batch_id, sidecar.serial)


# --- context: what else was bought that week, and what was scanned beside it -----------------------
@dataclass(frozen=True)
class ContextScan:
    """One document in a context strip, and where its scan can be seen."""

    filename: str
    verdict: str                     # accepted / marked / tossed, or "" while it is still being ingested
    name: str = ""
    date: str = ""
    time: str = ""
    cost: float = 0.0
    currency: str = ""
    image: str | None = None         # a media URL
    trim: Trim | None = None       # of the scan at ``image``
    receipt: str | None = None       # the archived document's id, for Receipt Detail
    current: bool = False


def _when(date: str, time: str) -> datetime | None:
    """A document's moment for the week strip: a week either side doesn't need the minute, so a date
    without a (usable) time still counts, at noon."""
    return parse_verdict_datetime(date, time) or parse_verdict_datetime(date, "12:00")


def _archived_url(rel_path: str) -> str:
    return "/api/media/archived/" + "/".join(quote(part) for part in rel_path.split("/"))


def _input_url(filename: str) -> str:
    return "/api/media/input/" + quote(filename)


def week_around(document: MarkedDocument, date: str, time: str, archived: list[dict],
                marked: list[MarkedDocument], ingesting: dict[str, ReviewDecision],
                scan_index: ScanIndex | None, trims: dict[str, Trim] | None = None) -> list[ContextScan] | None:
    """Receipts within a week either side of ``date``/``time`` (the form's values), in time order.

    A marked document is archived, so the week comes from the archive, the other marked documents and
    whatever is mid-ingest, not just its own batch. None when the form has no usable date yet. Tossed
    documents are left out. A document whose date isn't one
    (2025-02-31) is skipped rather than failing the whole strip. ``trims`` are the mid-ingest pages'.
    """
    trims = trims or {}
    target = _when(date, time)
    if target is None:
        return None
    lo, hi = target - WEEK_WINDOW, target + WEEK_WINDOW
    found: list[tuple[datetime, ContextScan]] = []

    def add(when: datetime | None, scan: ContextScan) -> None:
        if when is not None and lo <= when <= hi:
            found.append((when, scan))

    for record in archived:
        if record["document_type"] != "receipt":
            continue
        add(_when(record["date"], record["time"]), ContextScan(
            filename=record["filename"], verdict="accepted", name=record["name"], date=record["date"],
            time=record["time"], cost=float(record["cost"]), currency=record["currency"],
            image=_archived_url(record["path"]) if record["path"] else None,
            trim=Trim.model_validate(record["trim"]) if record.get("trim") else None,
            receipt=record["filename"]))

    for other in marked:
        review = other.first.review
        if other.key == document.key or review.document_type != "receipt":
            continue
        add(_when(review.date, review.time), ContextScan(
            filename=other.pages[0].name, verdict="marked", name=review.name, date=review.date, time=review.time,
            cost=review.cost, currency=review.currency, image=_archived_url(f"{MARKED}/{other.pages[0].name}"),
            trim=other.first.trim))

    files = {(batch.batch_id, serial): name for batch in (scan_index.batches if scan_index else [])
             for serial, name in batch.files.items()}
    for key, decision in ingesting.items():
        parsed = DocumentKey.parse(key)
        if decision.verdict == "tossed" or decision.document_type != "receipt" or parsed is None:
            continue
        first = files.get((parsed.batch_id, parsed.first_serial))
        add(_when(decision.date, decision.time), ContextScan(
            filename=first or key, verdict=decision.verdict, name=decision.name, date=decision.date,
            time=decision.time, cost=decision.cost, currency=decision.currency,
            image=_input_url(first) if first else None,
            trim=trims.get(batch_serial_key(parsed.batch_id, parsed.first_serial))))

    review = document.first.review
    found.append((target, ContextScan(
        filename=document.pages[0].name, verdict="marked", name=review.name, date=date, time=time,
        cost=review.cost, currency=review.currency, image=_archived_url(f"{MARKED}/{document.pages[0].name}"),
        trim=document.first.trim, current=True)))
    found.sort(key=lambda pair: pair[0])
    return [scan for _, scan in found]


def same_batch(output_path: Path, input_path: Path | None, document: MarkedDocument, scan_index: ScanIndex | None,
               tossed: set[str], accepted: dict[str, tuple[Sidecar, str]],
               trims: dict[str, Trim] | None = None) -> tuple[int | None, list[ContextScan]]:
    """Every scan of the document's batch in scan order, with where each one ended up. ``trims`` are the
    mid-ingest pages'."""
    trims = trims or {}
    batch_id = document.first.batch_id
    batch = next((b for b in (scan_index.batches if scan_index else []) if b.batch_id == batch_id), None)
    if batch is None:
        return batch_id, []
    here = set(document.filenames)
    scans = []
    for serial in sorted(batch.files):
        filename = batch.files[serial]
        filed = Path(filename).name     # a crop is "slices/<name>" in the batch, "<name>" in tossed/ and marked/
        current = filed in here
        if (output_path / MARKED / filed).is_file():
            sidecar = read_sidecar(output_path / MARKED / filed)
            review = sidecar.review if sidecar else None
            scans.append(ContextScan(
                filename=filename, verdict="marked", current=current,
                name=review.name if review else "", date=review.date if review else "",
                time=review.time if review else "", cost=review.cost if review else 0.0, currency=review.currency if review else "",
                image=_archived_url(f"{MARKED}/{filed}"), trim=sidecar.trim if sidecar else None))
        elif filename in accepted:
            sidecar, rel_path = accepted[filename]
            review = sidecar.review
            scans.append(ContextScan(
                filename=filename, verdict="accepted", current=current, name=review.name, date=review.date,
                time=review.time, cost=review.cost, currency=review.currency,
                image=_archived_url(rel_path) if rel_path else None, trim=sidecar.trim,
                receipt=sidecar.document_key or filename))
        elif filed in tossed:
            sidecar = read_sidecar(output_path / "tossed" / filed)
            scans.append(ContextScan(filename=filename, verdict="tossed", current=current,
                                     image=_archived_url(f"tossed/{filed}"),
                                     trim=sidecar.trim if sidecar else None))
        else:
            available = input_path is not None and (input_path / filename).is_file()
            scans.append(ContextScan(filename=filename, verdict="", current=current,
                                     image=_input_url(filename) if available else None,
                                     trim=trims.get(batch_serial_key(batch.batch_id, serial))))
    return batch_id, scans
