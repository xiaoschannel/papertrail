"""Every model call this machine has made, one JSON line each.

The sidecars keep what a document's own calls took; this keeps the run they were part of. One line per
call -- which job, which item, how long, what it consumed -- appended as it happens, so a batch's timing
survives however the jobs were arranged. Wall-clock time for a job is its first start to its last finish;
machine time is the sum of its lines, and the two stop agreeing the moment two jobs overlap.

It is never read by the pipeline, only appended to, so a corrupt or truncated line costs one row.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from models import ModelRun

FILE = "model_runs.jsonl"

_lock = threading.Lock()


def append(output_path: Path, kind: str, job_id: str, item: str, run: ModelRun) -> None:
    """Add one call to the log. Never raises: losing a line must not fail the work it describes."""
    row = {"kind": kind, "job_id": job_id, "item": item, **run.model_dump(exclude_none=True)}
    try:
        with _lock:
            with (output_path / FILE).open("a", encoding="utf-8") as lines:
                lines.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(output_path: Path) -> list[dict]:
    """The log, oldest first, skipping any line too damaged to read. For asking questions of a run."""
    path = output_path / FILE
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows.append(row)
    return rows
