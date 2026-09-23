"""Fix Rotation's queue: the scans to decide on across every unarchived batch, the decisions, and Undo.

Thin wrappers over rotation_review. Deciding rewrites a scan, so it waits for a job using that page's batch,
as the other edits do. The queue loads the orientation model (downloading it the first time); the sidebar's
count, which asks on every page, never does (``orientation.load_cached_model``).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

import archive_history
import rotation_review as rr
from api import ingest_store as store
from api.deps import get_input_path, get_output_path
from api.guards import no_job_running
from api.jobs import Claim, NOTHING_HELD
from api.schemas import (
    PageIn, RotationDecideIn, RotationDecisionOut, RotationItemOut, RotationQueueOut,
)
from data import load_rotation_decisions
from models import RotationDecision, RotationPrediction, parse_batch_serial_key
from orientation import ModelUnavailable, load_cached_model, load_model
from settings import get_config

router = APIRouter(prefix="/api/ingest/rotation", tags=["rotation"])


def orienter(download: bool) -> rr.Orient:
    """The cached orientation estimate, or None when the model can't be had (``download``: fetch it if it
    isn't on disk yet; otherwise only use it once it is)."""
    if download:
        try:
            load_model()
        except ModelUnavailable:
            return None
        return store.scan_orientation
    return store.scan_orientation if load_cached_model() else None


def queue_items(output_path: Path, input_path: Path, download: bool) -> tuple[bool, list[rr.QueueItem]]:
    orient = orienter(download)
    return orient is not None, rr.queue(output_path, input_path, orient, store.scan_skew, get_config().tilt_share)


def _claim(key: str) -> Claim:
    parsed = parse_batch_serial_key(key)
    return Claim(batches=frozenset({parsed[0]})) if parsed else NOTHING_HELD


def _refused(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc.args[0]))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="the scan is not in the scan folder")
    if isinstance(exc, archive_history.HistoryError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=409, detail="the scan is open elsewhere; try again in a moment")
    return HTTPException(status_code=409, detail=str(exc))      # a crop, a sliced sheet, a stale scan


@router.get("", response_model=RotationQueueOut)
def rotation_queue(output_path: Path = Depends(get_output_path), input_path: Path = Depends(get_input_path)):
    """Every scan to decide on, the latest decisions, and how many were fixed or left as they were."""
    checked, items = queue_items(output_path, input_path, download=True)
    # pages, by where each stands now: a fix undone counts as neither, a page fixed twice once
    decisions = [ds[-1] for ds in load_rotation_decisions(output_path).values() if ds]
    return RotationQueueOut(
        checked=checked,
        items=[RotationItemOut(**item.model_dump()) for item in items],
        recent=[RotationDecisionOut(decision=d, undoable=u) for d, u in rr.recent(output_path, input_path)],
        fixed=sum(d.action == "fixed" for d in decisions), left=sum(d.action == "left" for d in decisions))


@router.get("/prediction", response_model=RotationPrediction)
def prediction(key: str, output_path: Path = Depends(get_output_path), input_path: Path = Depends(get_input_path)):
    """What the detectors say of any page Fix Rotation can turn, flagged or not (its all-scans view)."""
    try:
        _, path = rr._page(output_path, input_path, key)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise _refused(exc) from exc
    return rr.predict(path, orienter(download=True), store.scan_skew, get_config().tilt_share)


@router.post("/decide", response_model=RotationDecision)
def decide(body: RotationDecideIn, output_path: Path = Depends(get_output_path),
           input_path: Path = Depends(get_input_path)):
    """Fix a scan (turn it upright, then straighten it) or leave it as it is; the decision is kept."""
    try:
        with no_job_running("turn scans", claim=_claim(body.key)):
            return rr.decide(output_path, input_path, body.key, body.image_version, body.fix, body.top_points,
                             body.degrees, body.source, orienter(download=False), store.scan_skew,
                             get_config().tilt_share)
    except (KeyError, FileNotFoundError, PermissionError, ValueError, archive_history.HistoryError) as exc:
        raise _refused(exc) from exc


@router.post("/undo", response_model=RotationDecision)
def undo(body: PageIn, output_path: Path = Depends(get_output_path), input_path: Path = Depends(get_input_path)):
    """Take back a page's last fix: its scan and trim as they were, and the scan judged again."""
    try:
        with no_job_running("turn scans", claim=_claim(body.key)):
            return rr.undo(output_path, input_path, body.key)
    except (KeyError, FileNotFoundError, PermissionError, ValueError, archive_history.HistoryError) as exc:
        raise _refused(exc) from exc
