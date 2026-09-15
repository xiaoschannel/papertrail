"""Background jobs for long pipeline steps (OCR, Parse, Archive), one at a time.

A job runs on a worker thread and reports progress through a :class:`JobContext`; the web page
follows it over Server-Sent Events and can cancel it (checked between items). There is one GPU, so
only one job may be queued or running at once — starting another is refused. Whatever happens, the
model manager unloads the job's model when it ends.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from api.model_manager import models

JobStatus = Literal["running", "succeeded", "failed", "cancelled"]
FINISHED: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
MAX_REPORTED_ERRORS = 200


class JobConflict(Exception):
    """Another job is still running."""


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: JobStatus = "running"
    total: int = 0
    done: int = 0
    failed: int = 0
    message: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False
    version: int = 0

    def snapshot(self) -> dict:
        end = self.finished_at or time.time()
        elapsed = end - self.started_at
        avg = elapsed / self.done if self.done else None
        eta = avg * (self.total - self.done) if avg is not None and self.status == "running" else None
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "message": self.message,
            "errors": list(self.errors),
            "elapsed_seconds": round(elapsed, 1),
            "seconds_per_item": round(avg, 2) if avg is not None else None,
            "eta_seconds": round(eta) if eta is not None else None,
            "cancel_requested": self.cancel_requested,
            "version": self.version,
        }


class JobContext:
    """What a job function uses to report progress and notice cancellation."""

    def __init__(self, runner: JobRunner, job: Job) -> None:
        self._runner = runner
        self._job = job

    @property
    def cancelled(self) -> bool:
        return self._job.cancel_requested

    def set_total(self, total: int) -> None:
        self._runner._update(self._job, total=total)

    def tick(self, ok: bool = True, item: str = "", error: str = "") -> None:
        def change(job: Job) -> None:
            job.done += 1
            if not ok:
                job.failed += 1
                if len(job.errors) < MAX_REPORTED_ERRORS:
                    job.errors.append({"item": item, "error": error})
        self._runner._mutate(self._job, change)

    def say(self, message: str) -> None:
        self._runner._update(self._job, message=message)


JobFn = Callable[[JobContext], str | None]


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._jobs: dict[str, Job] = {}
        self._current: Job | None = None
        # Held by edits that must not overlap a job, and while a job is planned and started, so
        # "no job is running" stays true for the whole edit (see exclusive()).
        self._edit_lock = threading.RLock()

    # --- starting and stopping --------------------------------------------------
    @contextmanager
    def exclusive(self, what: str, kind: str | None = None) -> Iterator[None]:
        """Run the body with no job (of ``kind``, or any) running and none able to start until it ends.

        Raises JobConflict if one is running. Starting a job inside the body is allowed (the lock is
        re-entrant), so a job can be planned and started atomically.
        """
        with self._edit_lock:
            job = self.running(kind)
            if job is not None:
                raise JobConflict(f"Can't {what} while {job['title']} is running.")
            yield

    def start(self, kind: str, title: str, fn: JobFn) -> dict:
        """Run ``fn`` on a worker thread. ``fn`` returns a final message (or None)."""
        with self._edit_lock, self._lock:
            if self._current is not None and self._current.status not in FINISHED:
                raise JobConflict(f"{self._current.title} is still running")
            job = Job(id=uuid.uuid4().hex, kind=kind, title=title)
            self._jobs[job.id] = job
            self._current = job
            snapshot = job.snapshot()
        threading.Thread(target=self._run, args=(job, fn), name=f"job-{kind}", daemon=True).start()
        return snapshot

    def cancel(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status not in FINISHED:
            self._update(job, cancel_requested=True, message="Cancelling after the current item…")
        return self.get(job_id)

    def _run(self, job: Job, fn: JobFn) -> None:
        ctx = JobContext(self, job)
        status: JobStatus = "failed"
        final = "The job stopped unexpectedly."
        try:
            message = fn(ctx)
            status = "cancelled" if job.cancel_requested else "succeeded"
            final = message or ""
        except Exception as exc:  # the job's own errors per item are reported via tick()
            final = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            # Unload first, so the next job can't start while this one's model is still resident;
            # always finish the job, or every later start would be refused as a conflict.
            try:
                models.release()
            finally:
                self._update(job, status=status, message=final, finished_at=time.time())

    # --- reading --------------------------------------------------------------------
    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def running(self, kind: str | None = None) -> dict | None:
        """The job still running (optionally only of ``kind``), or None."""
        job = self.current()
        if job is None or job["status"] in FINISHED or (kind is not None and job["kind"] != kind):
            return None
        return job

    def current(self) -> dict | None:
        """The running job, or the most recent one if none is running."""
        with self._lock:
            return self._current.snapshot() if self._current else None

    def wait_for_change(self, job_id: str, seen_version: int, timeout: float) -> dict | None:
        """Block until the job's version passes ``seen_version`` (or timeout); return its snapshot."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                job = self._jobs.get(job_id)
                if job is None:
                    return None
                remaining = deadline - time.monotonic()
                if job.version > seen_version or job.status in FINISHED or remaining <= 0:
                    return job.snapshot()
                self._changed.wait(remaining)

    def wait_until_finished(self, job_id: str, timeout: float = 30.0) -> dict | None:
        deadline = time.monotonic() + timeout
        snapshot = self.get(job_id)
        while snapshot and snapshot["status"] not in FINISHED and time.monotonic() < deadline:
            snapshot = self.wait_for_change(job_id, snapshot["version"], deadline - time.monotonic())
        return snapshot

    # --- internals ------------------------------------------------------------------------
    def _mutate(self, job: Job, change: Callable[[Job], None]) -> None:
        with self._changed:
            change(job)
            job.version += 1
            self._changed.notify_all()

    def _update(self, job: Job, **fields) -> None:
        def change(j: Job) -> None:
            for name, value in fields.items():
                setattr(j, name, value)
        self._mutate(job, change)


runner = JobRunner()
