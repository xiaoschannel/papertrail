"""Ingest step endpoints: File Index (batches, grouping, rotate, toss), OCR, Parse and Archive.

Thin wrappers over ingest_pipeline. OCR, Parse and Archive run as background jobs (api.jobs); starting
one while another runs is a 409. Steps that rewrite what a running job reads or writes are refused
while a job runs, too, and hold the runner's edit lock so no job is planned or started mid-edit.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

import ingest_pipeline as pipeline
from api import cache, ingest_registry as registry
from api import ingest_store as store
from api.deps import get_input_path, get_output_path
from api.guards import no_job_running
from api.jobs import runner
from api.model_manager import models
from api.schemas import (
    ArchiveMoveOut, ArchiveStatus, BatchFile, BatchOut, ConfirmIndexIn, GroupingOut, GroupingPageOut, IndexStatus,
    JobOut, OcrStatus, PageIn, ParseStatus, ProposedBatch, RotateIn, SaveGroupingIn, SaveGroupingOut, StartOcrIn,
    StartParseIn,
)
from indexing_schemes import SCHEMES
from models import ScanBatch
from settings import get_config, update_config

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _batch_out(batch: ScanBatch) -> BatchOut:
    return BatchOut(batch_id=batch.batch_id, start_datetime=batch.start_datetime,
                    end_datetime=batch.end_datetime, file_count=len(batch.files))


def _unarchived_batches(output_path: Path) -> list[BatchOut]:
    index = pipeline._load_index(output_path)
    return [_batch_out(b) for b in index.batches if not b.archived] if index else []


def _start(kind: str, title: str, prepare) -> dict:
    """Plan and start a job atomically: ``prepare()`` (run with no job running and no edit in progress)
    validates, plans and returns the job function."""
    with no_job_running(f"start {title}"):
        return runner.start(kind, title, prepare())


# --- File Index --------------------------------------------------------------------------------
@router.get("/index", response_model=IndexStatus)
def index_status(
    scheme: str | None = None,
    output_path: Path = Depends(get_output_path),
    input_path: Path = Depends(get_input_path),
):
    """Image counts and the batches the chosen indexing scheme would add."""
    schemes = list(SCHEMES)
    chosen = scheme or (get_config().indexing_scheme if get_config().indexing_scheme in SCHEMES else schemes[0])
    if chosen not in SCHEMES:
        raise HTTPException(status_code=422, detail=f"unknown indexing scheme: {chosen}")
    if not input_path.is_dir():
        return IndexStatus(blocker=f"The input image folder doesn't exist: {input_path}", schemes=schemes,
                           scheme=chosen, image_count=0, indexed_count=0, unindexed_count=0, existing_batches=0,
                           proposal=[], skipped=[], warnings=[], error=None, offending=[], token="")
    p = pipeline.propose_index(input_path, output_path, chosen)
    return IndexStatus(
        blocker=None, schemes=schemes, scheme=p.scheme, image_count=p.image_count, indexed_count=p.indexed_count,
        unindexed_count=p.unindexed_count, existing_batches=p.existing_batches,
        proposal=[ProposedBatch(**_batch_out(b).model_dump(),
                                files=[BatchFile(serial=s, filename=b.files[s]) for s in sorted(b.files)])
                  for b in p.batches],
        skipped=p.skipped, warnings=p.warnings, error=p.error, offending=p.offending, token=p.token,
    )


@router.post("/index", response_model=IndexStatus)
def confirm_index(
    body: ConfirmIndexIn,
    output_path: Path = Depends(get_output_path),
    input_path: Path = Depends(get_input_path),
):
    """Add the proposed batches to batches.json (only if the proposal is unchanged)."""
    try:
        with no_job_running("add batches"):
            pipeline.confirm_index(input_path, output_path, body.scheme, body.token)
    except pipeline.StaleProposal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    update_config(indexing_scheme=body.scheme)
    return index_status(body.scheme, output_path, input_path)


# --- Document grouping --------------------------------------------------------------------------
@router.get("/grouping", response_model=GroupingOut)
def grouping(
    batch_id: int | None = None,
    output_path: Path = Depends(get_output_path),
    input_path: Path = Depends(get_input_path),
):
    """A batch's pages in display order with their links, for grouping pages into documents.

    Without ``batch_id``, or when that batch has been archived meanwhile, the first unarchived batch.
    """
    batches = _unarchived_batches(output_path)
    if not batches:
        return GroupingOut(blocker="No unarchived batches. Add batches above first.", batches=[], batch_id=None,
                           pages=[], display_keys=[], active_links=[], saved_groups=[])
    chosen = batch_id if any(b.batch_id == batch_id for b in batches) else batches[0].batch_id
    try:
        state = pipeline.grouping_state(output_path, chosen)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    pages = []
    for page in state.pages:
        path = input_path / page.filename
        available = path.is_file()
        pages.append(GroupingPageOut(key=page.key, serial=page.serial, filename=page.filename, tossed=page.tossed,
                                     image_available=available,
                                     image_version=path.stat().st_mtime_ns if available else 0))
    return GroupingOut(blocker=None, batches=batches, batch_id=chosen, pages=pages, display_keys=state.display_keys,
                       active_links=state.active_links, saved_groups=state.saved_groups)


@router.put("/grouping", response_model=SaveGroupingOut)
def save_grouping(body: SaveGroupingIn, output_path: Path = Depends(get_output_path)):
    """Save multi-page groups. A change clears the batch's parse results and review decisions."""
    try:
        with no_job_running("change document grouping"), store.decisions_lock:
            changed = pipeline.save_grouping(output_path, body.batch_id, body.groups)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SaveGroupingOut(changed=changed)


def _set_tossed(body: PageIn, output_path: Path, tossed: bool) -> SaveGroupingOut:
    try:
        with no_job_running("toss or recover pages", kind="archive"), store.decisions_lock:
            pipeline.set_page_tossed(output_path, body.key, tossed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return SaveGroupingOut(changed=True)


@router.post("/pages/toss", response_model=SaveGroupingOut)
def toss_page(body: PageIn, output_path: Path = Depends(get_output_path)):
    """Toss the document this page belongs to."""
    return _set_tossed(body, output_path, True)


@router.post("/pages/recover", response_model=SaveGroupingOut)
def recover_page(body: PageIn, output_path: Path = Depends(get_output_path)):
    """Undo a toss."""
    return _set_tossed(body, output_path, False)


@router.post("/pages/rotate", response_model=SaveGroupingOut)
def rotate_page(
    body: RotateIn,
    output_path: Path = Depends(get_output_path),
    input_path: Path = Depends(get_input_path),
):
    """Rotate a page's scan in place so it is upright."""
    try:
        with no_job_running("rotate scans"):
            pipeline.rotate_page_image(output_path, input_path, body.key, body.top_points)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="the scan is not in the input folder") from exc
    return SaveGroupingOut(changed=True)


# --- OCR --------------------------------------------------------------------------------------------
@router.get("/ocr", response_model=OcrStatus)
def ocr_status(
    provider: str | None = None,
    batch_id: int | None = None,
    reprocess: bool = False,
    limit: int = Query(0, ge=0),
    output_path: Path = Depends(get_output_path),
    input_path: Path = Depends(get_input_path),
):
    """Page counts for the chosen scope; ``provider`` defaults to the saved OCR model."""
    providers = registry.ocr_providers()
    names = list(providers)
    configured = get_config().ocr_model
    if provider is not None and provider not in providers:
        raise HTTPException(status_code=422, detail=f"unknown OCR model: {provider}")
    chosen = provider or (configured if configured in providers else (names[0] if names else ""))
    blocker = None if (output_path / "batches.json").exists() else "Run File Index first to create batches.json."
    plan = pipeline.plan_ocr(output_path, input_path, batch_id, reprocess, limit)
    return OcrStatus(
        blocker=blocker, providers=names, provider=chosen,
        grounding=bool(getattr(providers.get(chosen), "grounding", False)) and get_config().extract_structured,
        batches=_unarchived_batches(output_path), total=plan.total, processed=plan.processed, failed=plan.failed,
        missing_images=plan.missing_images, to_process=len(plan.items),
    )


@router.post("/ocr", response_model=JobOut)
def start_ocr(
    body: StartOcrIn,
    output_path: Path = Depends(get_output_path),
    input_path: Path = Depends(get_input_path),
):
    """Start OCR on the planned pages as a background job."""
    providers = registry.ocr_providers()
    provider = providers.get(body.provider)
    if provider is None:
        raise HTTPException(status_code=422, detail=f"unknown OCR model: {body.provider}")

    def prepare():
        plan = pipeline.plan_ocr(output_path, input_path, body.batch_id, body.reprocess, body.limit)
        if not plan.items:
            raise HTTPException(status_code=422, detail="No pages to OCR.")
        update_config(ocr_model=body.provider)
        structured = bool(getattr(provider, "grounding", False)) and get_config().extract_structured

        def job(progress) -> str:
            models.acquire(f"ocr:{body.provider}", provider.teardown)
            return pipeline.run_ocr(output_path, plan.items, provider, structured, progress)
        return job

    return _start("ocr", f"OCR with {body.provider}", prepare)


# --- Parse ------------------------------------------------------------------------------------------
@router.get("/parse", response_model=ParseStatus)
def parse_status(
    reprocess: bool = False,
    limit: int = Query(0, ge=0),
    output_path: Path = Depends(get_output_path),
):
    names = list(registry.extractors())
    cfg = get_config()
    chosen = cfg.extractor_model if cfg.extractor_model in names else (names[0] if names else "")
    blocker = None if (output_path / "batches.json").exists() else "Run File Index first to create batches.json."
    plan = pipeline.plan_parse(output_path, reprocess, limit)
    if blocker is None and plan.total == 0:
        blocker = "No OCR results. Run OCR first."
    return ParseStatus(blocker=blocker, extractors=names, extractor=chosen,
                       custom_instruction=cfg.parse_custom_instruction, total=plan.total,
                       processed=plan.processed, tossed=plan.tossed, to_process=len(plan.documents))


@router.post("/parse", response_model=JobOut)
def start_parse(body: StartParseIn, output_path: Path = Depends(get_output_path)):
    """Start extraction on the planned documents as a background job."""
    extract = registry.extractors().get(body.extractor)
    if extract is None:
        raise HTTPException(status_code=422, detail=f"unknown extractor: {body.extractor}")

    def prepare():
        plan = pipeline.plan_parse(output_path, body.reprocess, body.limit)
        if not plan.documents:
            raise HTTPException(status_code=422, detail="No documents to parse.")
        update_config(extractor_model=body.extractor, parse_custom_instruction=body.custom_instruction)

        def job(progress) -> str:
            models.acquire(f"extract:{body.extractor}", registry.unload_extractor(body.extractor))
            return pipeline.run_parse(output_path, plan, extract, body.custom_instruction, progress)
        return job

    return _start("parse", f"Parse with {body.extractor}", prepare)


# --- Archive ----------------------------------------------------------------------------------------
@router.get("/archive", response_model=ArchiveStatus)
def archive_status(output_path: Path = Depends(get_output_path)):
    plan = pipeline.plan_archive(output_path)
    return ArchiveStatus(
        blocker=plan.blocker, unarchived_batches=plan.unarchived_batches, complete_batches=plan.complete_batches,
        documents=plan.documents, multipage=plan.multipage, files=plan.files, accepted=plan.accepted,
        marked=plan.marked, tossed=plan.tossed,
        moves=[ArchiveMoveOut(key=m.key, filename=m.filename, destination=m.destination) for m in plan.moves],
    )


@router.post("/archive", response_model=JobOut)
def start_archive(output_path: Path = Depends(get_output_path), input_path: Path = Depends(get_input_path)):
    """Archive every reviewed file as a background job."""

    def prepare():
        blocker = pipeline.plan_archive(output_path).blocker
        if blocker:
            raise HTTPException(status_code=422, detail=blocker)

        def job(progress) -> str:
            # Review and File Index refuse to change decisions while this runs (it deletes decisions.json).
            try:
                return pipeline.run_archive(output_path, input_path, progress)
            finally:
                cache.clear()  # the archive changed: visualize pages must re-read it
                store.clear()
        return job

    return _start("archive", "Archive", prepare)
