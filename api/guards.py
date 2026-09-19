"""Refuse edits that would collide with a running background job (HTTP 409)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from api.jobs import Claim, JobConflict, runner


@contextmanager
def no_job_running(what: str, kind: str | None = None, claim: Claim | None = None) -> Iterator[None]:
    """Hold for the whole edit: 409 if a running job holds what the edit touches, and none starts meanwhile.

    Say what the edit touches with ``claim`` (a batch, say) and only a job holding that refuses it;
    ``kind`` refuses it during jobs of that kind; with neither, any running job refuses it.
    """
    try:
        with runner.exclusive(what, kind, claim):
            yield
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@contextmanager
def planning_a_job() -> Iterator[None]:
    """Plan and start a job with no edit or other start in between; a refused start is a 409."""
    try:
        with runner.planning():
            yield
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
