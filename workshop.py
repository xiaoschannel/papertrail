"""The Marked Workshop: rescuing documents that review marked instead of accepting or tossing.

A marked document sits in ``marked/`` with one sidecar per page, each carrying the document key it
belongs to. The Streamlit workshop walked those files one at a time, so a two-page document appeared
twice and accepting one page filed it alone and stranded the other (issue #14). Here the pages are
grouped back into documents, and accepting or tossing moves all of them together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from data import load_smart_match_cache, read_sidecar, save_smart_match_cache
from dedupe_candidates import WEEK_WINDOW, parse_verdict_datetime
from document_files import MARKED, load_pages, place_document, toss_document
from document_grouping import rotate_file_upright
from models import (
    DocumentExtraction, DocumentKey, OcrResult, ReceiptResult, ReviewDecision, ScanIndex, Sidecar, batch_serial_key,
)
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


def marked_documents(output_path: Path) -> list[MarkedDocument]:
    """Everything in ``marked/``, grouped into documents and ordered by scan order."""
    folder = output_path / MARKED
    if not folder.is_dir():
        return []

    grouped: dict[str, list[tuple[Path, Sidecar]]] = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() == ".json":
            continue
        sidecar = read_sidecar(path)
        if sidecar is None:
            continue
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


def ocr_of(document: MarkedDocument) -> tuple[str, list]:
    """The document's stored OCR text (pages joined as Review does) and the first page's boxes."""
    parts = []
    for page_number, sidecar in enumerate(document.sidecars, start=1):
        if sidecar.ocr and sidecar.ocr.markdown:
            parts.append(f"--- Page {page_number} ---\n{sidecar.ocr.markdown}")
    boxes = document.first.ocr.boxes if document.first.ocr else None
    return "\n\n".join(parts), list(boxes or [])


def extractor_input(ocr_text: str, boxes: list) -> tuple[str, bool]:
    """What the extractor is given: box-annotated lines when OCR found boxes, else the plain text."""
    if not boxes:
        return ocr_text, False
    lines = ["--- Page 1 ---"]
    lines.extend(f"[P1-BOX-{index}] {box.text}" for index, box in enumerate(boxes))
    return "\n".join(lines), True


def store_reprocessed(output_path: Path, document: MarkedDocument, results: list[OcrResult],
                      extraction: DocumentExtraction, top_points: str = "") -> None:
    """Keep what a reprocess produced: each page's OCR, and the extraction on the first page.

    OCR read the pages turned by ``top_points``, so its boxes are measured on the turned image: the
    files are turned too, or the boxes would sit in the wrong places wherever the scan is shown later.
    Only the turn is kept; the treatments were only there to help OCR read, and change no geometry.
    """
    from data import write_sidecar

    for index, (path, sidecar) in enumerate(zip(document.pages, document.sidecars)):
        if index >= len(results):
            break
        rotate_file_upright(path, top_points)
        update = {"ocr": results[index]}
        if index == 0:
            update["extraction"] = extraction
        write_sidecar(path, sidecar.model_copy(update=update))


def accept(output_path: Path, document: MarkedDocument, decision: ReviewDecision,
           extraction: DocumentExtraction | None) -> list[str]:
    """File the document into the archive under ``decision`` and remember its confirmed name."""
    pages = load_pages(document.pages)

    def updated(sidecar: Sidecar) -> Sidecar:
        return sidecar.model_copy(update={"review": decision,
                                          "extraction": extraction if extraction is not None else sidecar.extraction})

    placed = place_document(output_path, pages, decision, sidecar_for=updated)
    _remember_name(output_path, document, decision, extraction)
    return placed


def toss(output_path: Path, document: MarkedDocument) -> list[str]:
    """Move every page of the document into ``tossed/``."""
    return toss_document(output_path, document.pages)


def _remember_name(output_path: Path, document: MarkedDocument, decision: ReviewDecision,
                   extraction: DocumentExtraction | None) -> None:
    sidecar = document.first
    key = sidecar.document_key or _page_key(sidecar) or sidecar.original_filename
    source = extraction if extraction is not None else sidecar.extraction
    cache = load_smart_match_cache(output_path)
    cache[key] = {
        "extracted": getattr(source, "name", "") or getattr(source, "title", ""),
        "confirmed": decision.name,
        "extracted_phone": getattr(source, "phone", "") if isinstance(source, ReceiptResult) else "",
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
                scan_index: ScanIndex | None) -> list[ContextScan] | None:
    """Receipts within a week either side of ``date``/``time`` (the form's values), in time order.

    Streamlit only looked at the batch mid-review; a marked document is archived, so the week comes
    from the archive, the other marked documents and whatever is mid-ingest. None when the form has no
    usable date yet. Tossed documents are left out, as they were. A document whose date isn't one
    (2025-02-31) is skipped rather than failing the whole strip.
    """
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
            image=_archived_url(record["path"]) if record["path"] else None, receipt=record["filename"]))

    for other in marked:
        review = other.first.review
        if other.key == document.key or review.document_type != "receipt":
            continue
        add(_when(review.date, review.time), ContextScan(
            filename=other.pages[0].name, verdict="marked", name=review.name, date=review.date, time=review.time,
            cost=review.cost, currency=review.currency, image=_archived_url(f"{MARKED}/{other.pages[0].name}")))

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
            image=_input_url(first) if first else None))

    review = document.first.review
    found.append((target, ContextScan(
        filename=document.pages[0].name, verdict="marked", name=review.name, date=date, time=time,
        cost=review.cost, currency=review.currency, image=_archived_url(f"{MARKED}/{document.pages[0].name}"),
        current=True)))
    found.sort(key=lambda pair: pair[0])
    return [scan for _, scan in found]


def same_batch(output_path: Path, input_path: Path | None, document: MarkedDocument, scan_index: ScanIndex | None,
               tossed: set[str], accepted: dict[str, tuple[Sidecar, str]]) -> tuple[int | None, list[ContextScan]]:
    """Every scan of the document's batch in scan order, with where each one ended up."""
    batch_id = document.first.batch_id
    batch = next((b for b in (scan_index.batches if scan_index else []) if b.batch_id == batch_id), None)
    if batch is None:
        return batch_id, []
    here = set(document.filenames)
    scans = []
    for serial in sorted(batch.files):
        filename = batch.files[serial]
        current = filename in here
        if (output_path / MARKED / filename).is_file():
            sidecar = read_sidecar(output_path / MARKED / filename)
            review = sidecar.review if sidecar else None
            scans.append(ContextScan(
                filename=filename, verdict="marked", current=current,
                name=review.name if review else "", date=review.date if review else "",
                time=review.time if review else "", cost=review.cost if review else 0.0, currency=review.currency if review else "",
                image=_archived_url(f"{MARKED}/{filename}")))
        elif filename in accepted:
            sidecar, rel_path = accepted[filename]
            review = sidecar.review
            scans.append(ContextScan(
                filename=filename, verdict="accepted", current=current, name=review.name, date=review.date,
                time=review.time, cost=review.cost, currency=review.currency,
                image=_archived_url(rel_path) if rel_path else None, receipt=sidecar.document_key or filename))
        elif filename in tossed:
            scans.append(ContextScan(filename=filename, verdict="tossed", current=current,
                                     image=_archived_url(f"tossed/{filename}")))
        else:
            available = input_path is not None and (input_path / filename).is_file()
            scans.append(ContextScan(filename=filename, verdict="", current=current,
                                     image=_input_url(filename) if available else None))
    return batch_id, scans
