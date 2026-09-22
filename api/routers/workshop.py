"""Marked Workshop: re-read a marked scan until it comes out right, then accept or toss it.

The form is Review's — same defaults, smart matches, hints and validation — because it is the same
decision, and the page reuses the same component. What the workshop adds is the image: rotate and
enhance the scan, then run OCR and extraction again as a background job, since it loads the models the
ingest jobs use. A reread is held (``workshop.rereads``) until the document is decided: Accept keeps it,
Toss or Discard drops it, and until then the page shows what it read.

Trimming a page is the exception: it is kept as soon as it is made (it changes nothing but what is read
and shown, and moves back out as easily), and the preview and the next reread read only the band.
"""

from __future__ import annotations

import io
from dataclasses import asdict, replace
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response

import review_logic as rl
import workshop
from api import cache, ingest_registry as registry
from api import ingest_store as store
from api.deps import get_output_path
from api.guards import no_job_running, planning_a_job
from api.jobs import Claim, runner
from api.model_manager import models
from api.schemas import (
    ContextScanOut, DecisionOut, FieldBoxOut, FormDefaultsOut, HintOut, HintsResponse, JobOut,
    MarkedDocumentOut, RereadOut, ReviewDocument, ReviewPage, SmartMatch, WorkshopContextOut, WorkshopDecisionIn,
    WorkshopHintsIn, WorkshopOut, WorkshopReprocessIn, WorkshopTrimIn,
)
from data import build_smart_match_history, load_decisions, load_trims, read_sidecar
from document_files import MARKED
from grounding import parse_grounding_output
from models import OcrResult, ReviewDecision, load_scan_index, turned_trim
from name_similarity import get_smart_match_candidates, quick_apply_label
from scan_enhance import Enhancement, enhance
from settings import get_config, update_config

router = APIRouter(prefix="/api/curate/workshop", tags=["workshop"])


def _document_or_404(output_path: Path, key: str) -> workshop.MarkedDocument:
    document = workshop.find(output_path, key)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no marked document {key}")
    return document


def _extraction(document: workshop.MarkedDocument, reread: workshop.Reread | None):
    return reread.extraction if reread else document.first.extraction


def _review_document(output_path: Path, document: workshop.MarkedDocument,
                     reread: workshop.Reread | None) -> ReviewDocument:
    """The marked document in Review's shape, so both pages share one form and one set of rules.

    With a reread pending, the text, the form's defaults and the boxes are the reread's. A reread's boxes
    are measured on the pages turned as it read them, so they are placed on the band the preview shows
    turned that way (``workshop.reading_trim``); stored boxes, on the page's own trim.
    """
    sidecar = document.first
    extraction = _extraction(document, reread)
    ocr_text = workshop.ocr_of(document, reread)
    defaults = rl.form_defaults(extraction) if extraction else rl.defaults_from_decision(sidecar.review)
    field_sources = getattr(extraction, "field_sources", {}) or {}
    results = reread.results if reread else [page.ocr for page in document.sidecars]
    turn = reread.top_points if reread else ""

    pages = []
    for page_number, (path, page) in enumerate(zip(document.pages, document.sidecars), start=1):
        ocr = results[page_number - 1] if page_number <= len(results) else None
        page_boxes = (ocr.boxes if ocr else None) or []
        # Every box the fields cite; with nothing cited, every box OCR found, so the scan still shows what was read.
        drawn = rl.field_boxes(page_number, page_boxes, field_sources) if field_sources \
            else rl.all_boxes(page_boxes)
        read_now = workshop.reading_trim(page.trim, turn)
        if ocr:
            drawn = rl.reframed(drawn, turned_trim(ocr.trim, turn), turned_trim(read_now, turn))
        pages.append(ReviewPage(
            file_key=path.name, filename=path.name, image_available=path.is_file(),
            boxes=FieldBoxOut.all_of(drawn),
            trim=page.trim,
            retrimmed=bool(ocr and ocr.succeeded and ocr.trim != read_now),
        ))

    history = build_smart_match_history(store.extractions(output_path), load_decisions(output_path),
                                        store.smart_match_cache(output_path))
    candidates = get_smart_match_candidates(defaults.name, defaults.phone, history)
    return ReviewDocument(
        key=document.key,
        defaults=FormDefaultsOut(**defaults.__dict__),
        initial_name=rl.initial_name(defaults.name, candidates),
        ocr_text=ocr_text,
        pages=pages,
        smart_matches=[SmartMatch(name=c.confirmed_name, name_score=c.name_score, phone_score=c.phone_score,
                                  quick_apply=c.quick_apply, label=quick_apply_label(c)) for c in candidates],
        decision=DecisionOut(**sidecar.review.model_dump()),
    )


@router.get("", response_model=WorkshopOut)
def workshop_queue(key: str | None = None, output_path: Path = Depends(get_output_path)):
    """Marked documents, and everything the form needs for the one being worked on."""
    documents = workshop.marked_documents(output_path)
    providers = registry.ocr_providers()
    extractors = registry.extractors()
    cfg = get_config()
    listed = [MarkedDocumentOut(key=d.key, pages=d.filenames, name=d.first.review.name,
                                comment=d.first.review.comment, batch_id=d.first.batch_id) for d in documents]
    # A key that no longer exists (decided in another tab, say) falls back to the first
    # document rather than leaving the page on an error it can't recover from.
    chosen = next((d for d in documents if d.key == key), None) or (documents[0] if documents else None)
    reread = workshop.rereads.get(chosen) if chosen else None
    return WorkshopOut(
        documents=listed,
        document=_review_document(output_path, chosen, reread) if chosen else None,
        reread=RereadOut(top_points=reread.top_points, ocr_model=reread.ocr_model,  # type: ignore[arg-type]
                         extractor=reread.extractor) if reread else None,
        ocr_models=list(providers), extractors=list(extractors),
        ocr_model=cfg.workshop_ocr_model if cfg.workshop_ocr_model in providers else next(iter(providers), ""),
        extractor=cfg.workshop_extractor_model if cfg.workshop_extractor_model in extractors
        else next(iter(extractors), ""),
    )


@router.get("/scan", response_class=Response,
            responses={200: {"content": {"image/png": {}}, "description": "the scan as OCR would see it"}})
def enhanced_scan(
    filename: str = Query(...),
    top_points: str = "",
    treatment: str = "none",
    clip: float = Query(3.0, ge=1.0, le=10.0),
    grid: int = Query(8, ge=2, le=16),
    contrast: float = Query(2.5, ge=0.5, le=3.0),
    gamma: float = Query(0.5, ge=0.2, le=3.0),
    lightness: int = Query(200, ge=128, le=255),
    chroma: int = Query(10, ge=1, le=80),
    output_path: Path = Depends(get_output_path),
):
    """The treated scan — the same pixels a reprocess would hand to OCR, so the preview can't lie: only the
    band the page is trimmed to, like the reread."""
    from PIL import Image

    folder = (output_path / MARKED).resolve()
    path = (folder / filename).resolve()
    if folder not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="no such marked scan")
    sidecar = read_sidecar(path) if path.suffix.lower() != ".json" else None
    settings = Enhancement(top_points=top_points, treatment=treatment, clip=clip, grid=grid,  # type: ignore[arg-type]
                           contrast=contrast, gamma=gamma, lightness=lightness, chroma=chroma,
                           trim=workshop.reading_trim(sidecar.trim if sidecar else None, top_points))
    try:
        with Image.open(path) as image:
            treated = enhance(image, settings)
    except OSError as exc:            # not an image: a sidecar, or a half-written file
        raise HTTPException(status_code=415, detail="that file isn't an image") from exc
    buffer = io.BytesIO()
    treated.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/context", response_model=WorkshopContextOut)
def context(key: str = Query(...), date: str = "", time: str = "", document_type: str = "receipt",
            output_path: Path = Depends(get_output_path)):
    """The week around the form's date and time, and the rest of the document's batch."""
    document = _document_or_404(output_path, key)
    try:
        scan_index = load_scan_index(output_path)
    except FileNotFoundError:
        scan_index = None
    week = None
    if document_type == "receipt":
        archived = cache.viz_records(output_path)
        records = archived[["filename", "path", "trim", "document_type", "name", "date", "time", "cost",
                            "currency"]].to_dict("records") if not archived.empty else []
        week = workshop.week_around(document, date, time, records, workshop.marked_documents(output_path),
                                    load_decisions(output_path), scan_index, load_trims(output_path))
    tossed, accepted = cache.archive_state(output_path)
    # optional: without a scan folder the batch still shows what was archived, marked or tossed
    input_path = Path(get_config().input_image_path) if get_config().input_image_path else None
    batch_id, batch = workshop.same_batch(output_path, input_path, document, scan_index, tossed, accepted,
                                          load_trims(output_path))
    return WorkshopContextOut(
        week=[ContextScanOut(**asdict(scan)) for scan in week] if week is not None else None,
        batch_id=batch_id,
        batch=[ContextScanOut(**asdict(scan)) for scan in batch],
    )


@router.post("/hints", response_model=HintsResponse)
def hints(body: WorkshopHintsIn, output_path: Path = Depends(get_output_path)):
    """The same hint rules, name status and accept blocker Review shows, for a marked document."""
    document = _document_or_404(output_path, body.key)
    draft = rl.Draft(document_type=body.draft.document_type, name=body.draft.name, date=body.draft.date,
                     time=body.draft.time, cost=body.draft.cost, currency=body.draft.currency)
    extraction = _extraction(document, workshop.rereads.get(document))
    drafted = rl.draft_extraction(extraction, draft) if extraction else rl.draft_extraction(
        rl.form_defaults_extraction(draft), draft)
    history = build_smart_match_history(store.extractions(output_path), load_decisions(output_path),
                                        store.smart_match_cache(output_path))
    confirmed = {row.confirmed for row in history}
    return HintsResponse(
        hints=[HintOut(message=hint.message, color=hint.color) for hint in rl.hints_for(drafted)],
        name_status=rl.name_status(draft.name, confirmed),
        accept_error=rl.accept_error(draft),
    )


@router.post("/reprocess", response_model=JobOut)
def reprocess(body: WorkshopReprocessIn, output_path: Path = Depends(get_output_path)):
    """Read the treated scan again and extract from it, as a job: it loads the same models as ingest.

    The result waits for the decision (see ``workshop.Reread``); nothing on disk changes yet.
    """
    document = _document_or_404(output_path, body.key)
    providers = registry.ocr_providers()
    provider = providers.get(body.ocr_model)
    extract = registry.extractors().get(body.extractor)
    if provider is None or extract is None:
        raise HTTPException(status_code=422, detail="unknown OCR model or extractor")
    update_config(workshop_ocr_model=body.ocr_model, workshop_extractor_model=body.extractor)
    structured = bool(getattr(provider, "grounding", False)) and get_config().extract_structured
    settings = Enhancement(top_points=body.top_points, treatment=body.treatment, clip=body.clip, grid=body.grid,
                           contrast=body.contrast, gamma=body.gamma, lightness=body.lightness, chroma=body.chroma)

    def job(progress) -> str:
        from PIL import Image

        progress.set_total(len(document.pages) + 1)
        results: list[OcrResult] = []
        for page, sidecar in zip(document.pages, document.sidecars):
            treated_path = page.with_name(f"{page.stem}.enhanced.png")
            band = workshop.reading_trim(sidecar.trim, body.top_points)
            try:
                with Image.open(page) as image:
                    enhance(image, replace(settings, trim=band)).save(treated_path)
                models.acquire(f"ocr:{body.ocr_model}", provider.teardown)
                markdown = provider.run(treated_path, structured=False)
                boxes = parse_grounding_output(provider.run(treated_path, structured=True)) if structured else []
            finally:
                treated_path.unlink(missing_ok=True)
            results.append(OcrResult(markdown=markdown, boxes=boxes or None, trim=band))
            progress.tick(item=page.name)

        models.acquire(f"extract:{body.extractor}", registry.unload_extractor(body.extractor))
        text, has_boxes = workshop.extractor_input(results)
        extraction = extract(text, has_boxes=has_boxes, custom_instruction=get_config().parse_custom_instruction)
        progress.tick(item=document.key)

        workshop.rereads.put(document.key, workshop.Reread(
            pages=tuple(document.filenames), results=results, extraction=extraction, top_points=body.top_points,
            ocr_model=body.ocr_model, extractor=body.extractor))
        return f"Re-read {len(results)} page(s) of {document.key}. Accept keeps it; nothing is saved until then."

    # Marked documents are already archived, so no batch is involved: the run holds only the GPU. It is a
    # couple of model calls, with nothing to stop between, so it can't be cancelled.
    with planning_a_job():
        return runner.start("workshop", f"Reprocess {document.key}", job, Claim(gpu=True), cancellable=False)


@router.put("/trim", response_model=WorkshopOut)
def trim_page(body: WorkshopTrimIn, key: str = Query(...), output_path: Path = Depends(get_output_path)):
    """Keep only a band of one page of a marked document (or, with no trim, all of it), right away.

    The scan is untouched. The preview and the next reread read only the band; a reread already waiting
    keeps what it read, and its boxes are placed on the new band.
    """
    page = workshop.marked_page(output_path, body.filename)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no marked scan {body.filename}")
    with no_job_running("trim a marked scan", kind="archive"):
        workshop.trim_page(*page, body.trim)
    cache.clear()   # the archive's displays show the band
    return workshop_queue(key=key, output_path=output_path)


@router.delete("/reread", response_model=WorkshopOut)
def discard_reread(key: str = Query(...), output_path: Path = Depends(get_output_path)):
    """Drop a pending reread: the document goes back to what its sidecars say."""
    workshop.rereads.drop(key)
    return workshop_queue(key=key, output_path=output_path)


@router.post("/decide", response_model=WorkshopOut)
def decide(body: WorkshopDecisionIn, output_path: Path = Depends(get_output_path)):
    """Accept the document into the archive, or toss it. Every page of it moves together."""
    document = _document_or_404(output_path, body.key)
    if body.verdict == "tossed":
        with no_job_running("toss a marked document", kind=("archive", "workshop")):
            workshop.toss(output_path, document)
    else:
        draft = rl.Draft(document_type=body.draft.document_type, name=body.draft.name, date=body.draft.date,
                         time=body.draft.time, cost=body.draft.cost, currency=body.draft.currency)
        error = rl.accept_error(draft)
        if error:
            raise HTTPException(status_code=422, detail=error)
        receipt = draft.document_type == "receipt"
        decision = ReviewDecision(
            verdict=body.verdict, document_type=draft.document_type, name=draft.name, date=draft.date,
            time=draft.time, cost=(draft.cost or 0.0) if receipt else 0.0,
            currency=draft.currency if receipt else "", comment=body.comment)
        with no_job_running("file a marked document", kind=("archive", "workshop")):
            workshop.accept(output_path, document, decision, workshop.rereads.get(document))

    cache.clear()   # the archive gained (or tossed) a document
    store.clear()
    return workshop_queue(output_path=output_path)
