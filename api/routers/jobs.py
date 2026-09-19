"""Background job status, cancellation and live progress (Server-Sent Events)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.jobs import FINISHED, JobConflict, runner
from api.schemas import JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

#: How long one wait for a change lasts before the stream sends a keep-alive comment.
HEARTBEAT_SECONDS = 15.0


@router.get("", response_model=list[JobOut])
def jobs():
    """Every running job, plus the last finished job of each kind with none running - what the pages
    show: live progress, or how their last run ended."""
    return runner.recent()


@router.get("/current", response_model=JobOut | None)
def current_job():
    """A running job (the latest started), or the last one to run; None before any job. Several jobs can
    run at once - ``GET /api/jobs`` lists them."""
    return runner.current()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str):
    """Ask the job to stop after the item it is working on."""
    try:
        job = runner.cancel(job_id)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


@router.get(
    "/{job_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}},
                     "description": "A `data:` event with the job (JobOut JSON) whenever it changes; "
                                    "the stream ends after the job finishes."}},
)
async def job_events(job_id: str, request: Request):
    if runner.get(job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")

    async def stream():
        seen = -1
        while True:
            if await request.is_disconnected():
                return
            job = await asyncio.to_thread(runner.wait_for_change, job_id, seen, HEARTBEAT_SECONDS)
            if job is None:
                return
            if job["version"] == seen and job["status"] not in FINISHED:
                yield ": keep-alive\n\n"
                continue
            seen = job["version"]
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] in FINISHED:
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
