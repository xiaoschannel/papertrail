"""The Dev pages: Sanity Check, Index Audit and the Experiment bench.

Sanity Check and Index Audit are read-only views of ``archive_audit``. Experiment runs one uploaded
image through OCR and Parse as background jobs — they load the same models as ingest — and keeps what
they return in a scratch folder (``experiment_runs``), never in the archive.
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile

import experiment_runs as runs
import review_logic as rl
from api import ingest_registry as registry
from api.deps import get_output_path
from api.guards import planning_a_job
from api.jobs import NOTHING_HELD, Claim, runner
from api.model_manager import models
from api.schemas import (
    BatchSanityOut, BatchStatOut, DuplicateFilenameOut, ExperimentOcrIn, ExperimentOcrOut, ExperimentOut,
    ExperimentParseIn, ExperimentParseOut, ExperimentPromptIn, ExperimentPromptOut, ExperimentRunOut,
    ExperimentTreatmentIn, FieldBoxOut, IndexAuditOut, IndexFileOut, InputComparisonOut, JobOut, PriceOut, SanityOut,
    SidecarMismatchOut,
)
from archive_audit import (
    batch_coverage, batch_statistics, check_archive_sidecars, count_duplicate_filenames, disk_vs_index_delta,
)
from data import scan_organized_filenames
from extraction import PRICES, call_extractor, cost_of, extraction_messages
from grounding import parse_grounding_output
from models import TokenUse, iter_indexed_files, load_scan_index
from scan_enhance import Enhancement, enhance
from slicing import SLICES_DIR
from settings import IMAGE_EXTENSIONS, get_config, update_config

router = APIRouter(prefix="/api/dev", tags=["dev"])


def _input_folder() -> Path | None:
    configured = get_config().input_image_path
    return Path(configured) if configured and Path(configured).is_dir() else None


# --- Sanity Check ---------------------------------------------------------------------------------------
@router.get("/sanity", response_model=SanityOut)
def sanity(output_path: Path = Depends(get_output_path)):
    """Is every indexed file where it should be, and does every archived scan have its sidecar?"""
    mismatches = [SidecarMismatchOut(folder=folder, missing_sidecar=missing, extra_sidecar=extra)
                  for folder, missing, extra in sorted(check_archive_sidecars(output_path))] \
        if output_path.is_dir() else []
    try:
        scan_index = load_scan_index(output_path)
    except FileNotFoundError:
        return SanityOut(indexed=False, batches=[], sidecar_mismatches=mismatches)

    coverage = {row["batch_id"]: row for row in batch_coverage(scan_index, scan_organized_filenames(output_path))}
    input_path = _input_folder()
    batches = []
    for batch in scan_index.batches:
        row = coverage[batch.batch_id]
        missing_from_input = None
        if not batch.archived and input_path is not None:
            missing_from_input = sorted(name for name in set(batch.files.values())
                                        if not (input_path / name).is_file())
        batches.append(BatchSanityOut(
            batch_id=batch.batch_id, archived=batch.archived, files=row["in_batch"], organized=row["organized"],
            missing_from_archive=row["missing"] if batch.archived else [],
            missing_from_input=missing_from_input,
        ))
    return SanityOut(indexed=True, batches=batches, sidecar_mismatches=mismatches)


# --- Index Audit ----------------------------------------------------------------------------------------
def _local_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")


@router.get("/index-audit", response_model=IndexAuditOut)
def index_audit(output_path: Path = Depends(get_output_path)):
    """What batches.json says: totals, filenames indexed twice, each batch, and the scan folder against it."""
    batches_path = output_path / "batches.json"
    try:
        scan_index = load_scan_index(output_path)
    except FileNotFoundError:
        return IndexAuditOut(index_file=None, total_batches=0, archived=0, non_archived=0, total_entries=0,
                             unique_filenames=0, lost_to_dedup=0, duplicates=[], batches=[], input=None)

    stat = batches_path.stat()
    stats = batch_statistics(scan_index)
    entries = iter_indexed_files(scan_index, include_archived=True)
    batch_ids_by_name: dict[str, list[int]] = {}
    for batch_id, _serial, filename in entries:
        batch_ids_by_name.setdefault(filename, []).append(batch_id)

    comparison = None
    input_path = _input_folder()
    if input_path is not None:
        on_disk = {f.name for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS}
        crops = input_path / SLICES_DIR   # sliced sheets' crops, indexed as "slices/<name>"
        if crops.is_dir():
            on_disk |= {f"{SLICES_DIR}/{f.name}" for f in crops.iterdir()
                        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS}
        indexed_not_on_disk, on_disk_not_indexed = disk_vs_index_delta(on_disk, set(batch_ids_by_name))
        comparison = InputComparisonOut(on_disk=len(on_disk), indexed_not_on_disk=len(indexed_not_on_disk),
                                        on_disk_not_indexed=sorted(on_disk_not_indexed))

    return IndexAuditOut(
        index_file=IndexFileOut(size=stat.st_size, modified=_local_time(stat.st_mtime)),
        total_batches=stats["total_batches"], archived=stats["archived"], non_archived=stats["non_archived"],
        total_entries=stats["total_entries"], unique_filenames=stats["unique_filenames"],
        lost_to_dedup=stats["lost_to_dedup"],
        duplicates=[DuplicateFilenameOut(filename=name, count=count, batch_ids=batch_ids_by_name[name])
                    for name, count in sorted(count_duplicate_filenames(scan_index).items())],
        batches=[BatchStatOut(**row) for row in stats["per_batch"]],
        input=comparison,
    )


# --- Experiment -----------------------------------------------------------------------------------------
def _run_or_404(run_id: str) -> runs.Run:
    run = runs.find(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That experiment image is gone (only the latest few are kept).")
    return run


def _enhancement(body: ExperimentTreatmentIn) -> Enhancement:
    return Enhancement(**body.model_dump(include=set(ExperimentTreatmentIn.model_fields)))


@router.get("/experiment", response_model=ExperimentOut)
def experiment():
    providers = registry.ocr_providers()
    extractors = registry.extractors()
    cfg = get_config()
    latest = runs.latest()
    return ExperimentOut(
        ocr_models=list(providers),
        grounding_models=[name for name, provider in providers.items() if getattr(provider, "grounding", False)],
        extractors=list(extractors),
        local_extractors=[name for name in extractors if registry.extractor_needs_gpu(name)],
        # the pipeline's own models: the bench starts from them (and saves back to them)
        ocr_model=cfg.ocr_model if cfg.ocr_model in providers else next(iter(providers), ""),
        extractor=cfg.extractor_model if cfg.extractor_model in extractors else next(iter(extractors), ""),
        with_boxes=cfg.extract_structured,
        custom_instruction=cfg.parse_custom_instruction,
        latest=latest.id if latest else None,
        prices={name: PriceOut(**price.model_dump()) for name, price in PRICES.items() if name in extractors},
    )


#: Far above any scan (a full-page 600 dpi colour scan is a few tens of MB).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@router.post("/experiment", response_model=ExperimentRunOut)
async def upload(file: UploadFile):
    """Start a run from an uploaded image. It's kept in a scratch folder, not the archive."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"That file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        run = runs.create(file.filename or "image.png", data)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return _run_out(run)


@router.get("/experiment/{run_id}", response_model=ExperimentRunOut)
def run_detail(run_id: str):
    return _run_out(_run_or_404(run_id))


def _run_out(run: runs.Run) -> ExperimentRunOut:
    from PIL import Image

    os.utime(run.folder)                  # opened = used: the runs used last are the ones kept
    with Image.open(run.image) as image:
        width, height = image.size
    ocr = runs.load_ocr(run)
    parse = runs.load_parse(run) if ocr else None
    ocr_out = None
    if ocr:
        ocr_out = ExperimentOcrOut(
            model=ocr.model, read_at=ocr.read_at, with_boxes=ocr.with_boxes, treatment=ExperimentTreatmentIn(**ocr.treatment),
            markdown=ocr.markdown, structured_raw=ocr.structured_raw,
            boxes=FieldBoxOut.all_of(rl.all_boxes(ocr.boxes)),
            seconds=ocr.seconds, structured_seconds=ocr.structured_seconds)
    parse_out = None
    if ocr and parse:
        sources = getattr(parse.extraction, "field_sources", {}) or {}
        parse_out = ExperimentParseOut(
            extractor=parse.extractor, custom_instruction=parse.custom_instruction,
            extraction=parse.extraction, seconds=parse.seconds, tokens=parse.tokens,
            cost=cost_of(parse.extractor, parse.tokens) if parse.tokens else None,
            field_boxes=FieldBoxOut.all_of(rl.field_boxes(1, ocr.boxes, sources)))
    return ExperimentRunOut(id=run.id, filename=run.image.name, width=width, height=height,
                            ocr=ocr_out, parse=parse_out)


_PNG = {200: {"content": {"image/png": {}}, "description": "a PNG image"}}


def _png(image) -> Response:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/experiment/{run_id}/scan", response_class=Response, responses=_PNG)
def treated_scan(run_id: str, treatment: Annotated[ExperimentTreatmentIn, Query()]):
    """The image with a treatment applied — the pixels an OCR run with these settings would read."""
    from PIL import Image

    run = _run_or_404(run_id)
    with Image.open(run.image) as image:
        return _png(enhance(image, _enhancement(treatment)))


@router.get("/experiment/{run_id}/seen", response_class=Response, responses=_PNG)
def seen_scan(run_id: str):
    """The image the last OCR run read. Its boxes were measured on it, whatever the controls say now."""
    run = _run_or_404(run_id)
    if not run.seen.is_file():
        raise HTTPException(status_code=404, detail="OCR hasn't read this image yet.")
    return Response(content=run.seen.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/experiment/{run_id}/prompt", response_model=ExperimentPromptOut)
def prompt(run_id: str, body: ExperimentPromptIn):
    """The prompt Parse would send for the current OCR text with these instructions."""
    ocr = runs.load_ocr(_run_or_404(run_id))
    if ocr is None:
        raise HTTPException(status_code=409, detail="Run OCR first: the prompt is built from its text.")
    text, has_boxes = runs.extractor_input(ocr)
    # Shown as it is sent: two messages, since the break between them is the part a model can cache.
    messages = extraction_messages(text, has_boxes, custom_instruction=body.custom_instruction)
    shown = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
    return ExperimentPromptOut(prompt=shown, has_boxes=has_boxes)


@router.post("/experiment/{run_id}/ocr", response_model=JobOut)
def run_ocr(run_id: str, body: ExperimentOcrIn):
    """Read the treated image with one OCR model, as a job: the model loads onto the GPU."""
    run = _run_or_404(run_id)
    provider = registry.ocr_providers().get(body.model)
    if provider is None:
        raise HTTPException(status_code=422, detail=f"unknown OCR model {body.model}")
    update_config(ocr_model=body.model)   # the model tried becomes the pipeline's next
    with_boxes = body.with_boxes and bool(getattr(provider, "grounding", False))
    settings = _enhancement(body)

    def job(progress) -> str:
        from PIL import Image

        progress.set_total(2 if with_boxes else 1)
        with Image.open(run.image) as image:
            enhance(image, settings).save(run.ocr_input)
        try:
            models.acquire(f"ocr:{body.model}", provider.teardown)
            started = time.perf_counter()
            markdown = provider.run(run.ocr_input, structured=False)
            seconds = time.perf_counter() - started
            progress.tick(item="text")
            raw, structured_seconds = None, None
            if with_boxes:
                if progress.cancelled:
                    return "Cancelled before the boxes pass; nothing was kept."
                started = time.perf_counter()
                raw = provider.run(run.ocr_input, structured=True)
                structured_seconds = time.perf_counter() - started
                progress.tick(item="boxes")
            boxes = parse_grounding_output(raw) if raw is not None else []
            runs.save_ocr(run, runs.OcrRecord(
                model=body.model, read_at=time.time(), with_boxes=with_boxes,
                treatment=body.model_dump(include=set(ExperimentTreatmentIn.model_fields)),
                markdown=markdown,
                structured_raw=raw, boxes=boxes, seconds=round(seconds, 2),
                structured_seconds=round(structured_seconds, 2) if structured_seconds is not None else None,
            ), seen=run.ocr_input)
        finally:
            run.ocr_input.unlink(missing_ok=True)
        return f"Read {run.image.name} with {body.model}" + (f": {len(boxes)} boxes." if with_boxes else ".")

    with planning_a_job():
        return runner.start("experiment-ocr", f"Experiment: OCR with {body.model}", job, Claim(gpu=True))


@router.post("/experiment/{run_id}/parse", response_model=JobOut)
def run_parse(run_id: str, body: ExperimentParseIn):
    """Extract from the last OCR reading, as Parse would. A hosted model holds nothing locally."""
    run = _run_or_404(run_id)
    extract = registry.extractors().get(body.extractor)
    if extract is None:
        raise HTTPException(status_code=422, detail=f"unknown extractor {body.extractor}")
    ocr = runs.load_ocr(run)
    if ocr is None:
        raise HTTPException(status_code=409, detail="Run OCR first: Parse reads its text.")
    update_config(extractor_model=body.extractor, parse_custom_instruction=body.custom_instruction)
    local = registry.extractor_needs_gpu(body.extractor)
    text, has_boxes = runs.extractor_input(ocr)

    def job(progress) -> str:
        progress.set_total(1)
        if local:
            models.acquire(f"extract:{body.extractor}", registry.unload_extractor(body.extractor))
        used: list[TokenUse] = []
        started = time.perf_counter()
        extraction = call_extractor(extract, text, has_boxes, body.custom_instruction, on_usage=used.append)
        seconds = time.perf_counter() - started
        progress.tick(item=run.image.name)
        runs.save_parse(run, runs.ParseRecord(extractor=body.extractor, custom_instruction=body.custom_instruction,
                                              extraction=extraction, seconds=round(seconds, 2),
                                              tokens=used[0] if used else None))
        return f"Extracted {run.image.name} with {body.extractor}."

    with planning_a_job():
        return runner.start("experiment-parse", f"Experiment: Parse with {body.extractor}", job,
                            Claim(gpu=True) if local else NOTHING_HELD, cancellable=False)
