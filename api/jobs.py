"""Background jobs for long pipeline steps (OCR, Parse, Archive), run side by side when they can be.

A job runs on a worker thread and reports progress through a :class:`JobContext`; the web page
follows it over Server-Sent Events and can cancel it (checked between items).

Jobs run at the same time unless they would collide, and what decides that is a :class:`Claim` —
what the job holds while it runs: the batches it works on, the GPU if it loads a model onto it, or
everything (Archive: it moves every reviewed file and deletes the working files). So OCR can read
batch 12 while Parse extracts batch 11 with a hosted model and you review batch 10; a second job that
wants the GPU, or a batch someone else holds, is refused and told who holds it. Edits claim the same
way (see ``api.guards``), so regrouping batch 13 doesn't wait for OCR on batch 12.

Whatever happens, a job that took the GPU unloads its model when it ends.
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
    """What was asked for is held by a running job."""


@dataclass(frozen=True)
class Claim:
    """What a job holds while it runs, or what an edit is about to touch.

    Two claims collide when they share a batch, both need the GPU (one card: two models don't fit),
    or either claims ``everything``.
    """

    batches: frozenset[int] = frozenset()
    gpu: bool = False
    everything: bool = False

    def collides_with(self, other: Claim) -> bool:
        return (self.everything or other.everything or (self.gpu and other.gpu)
                or bool(self.batches & other.batches))

    def shared_with(self, other: Claim) -> str:
        """What the two claims fight over, in words, for the message that explains a refusal."""
        if self.gpu and other.gpu:
            return "the GPU"
        shared = sorted(self.batches & other.batches)
        if shared:
            return (f"batch {shared[0]}" if len(shared) == 1
                    else "batches " + ", ".join(map(str, shared[:-1])) + f" and {shared[-1]}")
        return "the archive"


#: Archive, and any job that hasn't said what it touches: collides with every other claim.
EVERYTHING = Claim(everything=True)
#: An edit that only collides with a job holding everything (adding a batch: no job holds it yet).
NOTHING_HELD = Claim()


@dataclass
class Job:
    id: str
    kind: str
    title: str
    claim: Claim = EVERYTHING
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
            # what it holds, so pages can tell which of their controls it locks
            "batches": sorted(self.claim.batches),
            "gpu": self.claim.gpu,
            "everything": self.claim.everything,
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
        self._order: list[str] = []          # start order, oldest first
        # Held while an edit checks and makes its change, and while a job is planned and started, so
        # what the check saw stays true until the change is done (see exclusive() and planning()).
        self._edit_lock = threading.RLock()

    # --- starting and stopping --------------------------------------------------
    @contextmanager
    def planning(self) -> Iterator[None]:
        """Hold off edits and other starts while a job is planned from what's on disk and started."""
        with self._edit_lock:
            yield

    @contextmanager
    def exclusive(self, what: str, kind: str | None = None, claim: Claim | None = None) -> Iterator[None]:
        """Run the body with nothing it touches held by a job, and no such job able to start until it ends.

        Refused (JobConflict) if a running job holds what ``claim`` touches; with ``kind`` instead, if a
        job of that kind runs; with neither, if any job runs. Starting a job inside the body is allowed
        (the lock is re-entrant).
        """
        with self._edit_lock:
            for job in self._running():
                if kind is not None:
                    hit = job.kind == kind
                elif claim is not None:
                    hit = claim.collides_with(job.claim)
                else:
                    hit = True
                if hit:
                    held = claim.shared_with(job.claim) if claim is not None else None
                    raise JobConflict(f"Can't {what} while {job.title} is running." if held in (None, "the archive")
                                      else f"Can't {what} while {job.title} is using {held}.")
            yield

    def start(self, kind: str, title: str, fn: JobFn, claim: Claim = EVERYTHING) -> dict:
        """Run ``fn`` on a worker thread, holding ``claim``. ``fn`` returns a final message (or None).

        Refused if a job of the same kind runs (each page follows one job of its kind) or a running job
        holds anything ``claim`` needs.
        """
        with self._edit_lock, self._lock:
            for other in self._running_locked():
                if other.kind == kind:
                    raise JobConflict(f"{other.title} is still running.")
                if claim.collides_with(other.claim):
                    held = claim.shared_with(other.claim)
                    raise JobConflict(f"{title} has to wait: {other.title} is running." if held == "the archive"
                                      else f"{title} has to wait: {other.title} is using {held}.")
            job = Job(id=uuid.uuid4().hex, kind=kind, title=title, claim=claim)
            self._jobs[job.id] = job
            self._order.append(job.id)
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
            # Unload first, so the next GPU job can't start while this one's model is still resident;
            # always finish the job, or everything it held would stay held. Only a job that held the GPU
            # unloads: a hosted-model Parse ending must not unload the model an OCR run is still using.
            try:
                if job.claim.gpu or job.claim.everything:
                    models.release()
            finally:
                self._update(job, status=status, message=final, finished_at=time.time())

    # --- reading --------------------------------------------------------------------
    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def _running_locked(self) -> list[Job]:
        return [self._jobs[i] for i in self._order if self._jobs[i].status not in FINISHED]

    def _running(self) -> list[Job]:
        with self._lock:
            return self._running_locked()

    def running(self, kind: str | None = None) -> dict | None:
        """A job still running (optionally only of ``kind``), or None."""
        with self._lock:
            job = next((j for j in self._running_locked() if kind is None or j.kind == kind), None)
            return job.snapshot() if job else None

    def running_jobs(self) -> list[dict]:
        with self._lock:
            return [job.snapshot() for job in self._running_locked()]

    def held_batches(self) -> frozenset[int]:
        """Batches running jobs hold, so a job being planned can leave them out."""
        with self._lock:
            return frozenset().union(*(job.claim.batches for job in self._running_locked()))

    def recent(self) -> list[dict]:
        """Every running job, plus the latest finished job of each kind that has none running — what
        the pages show: live progress, or how their last run ended."""
        with self._lock:
            jobs = [self._jobs[i] for i in reversed(self._order)]
            running_kinds = {j.kind for j in jobs if j.status not in FINISHED}
            shown, seen = [], set()
            for job in jobs:
                if job.status not in FINISHED:
                    shown.append(job)
                elif job.kind not in running_kinds and job.kind not in seen:
                    shown.append(job)
                    seen.add(job.kind)
            return [job.snapshot() for job in reversed(shown)]

    def current(self) -> dict | None:
        """A running job (the latest started), or the latest one if none is running."""
        with self._lock:
            running = self._running_locked()
            job = running[-1] if running else (self._jobs[self._order[-1]] if self._order else None)
            return job.snapshot() if job else None

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
