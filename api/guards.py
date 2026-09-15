"""Refuse edits that would collide with a running background job (HTTP 409)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from api.jobs import JobConflict, runner


@contextmanager
def no_job_running(what: str, kind: str | None = None) -> Iterator[None]:
    """Hold for the whole edit: 409 if a job (of ``kind``, or any) runs, and no job starts meanwhile."""
    try:
        with runner.exclusive(what, kind):
            yield
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
