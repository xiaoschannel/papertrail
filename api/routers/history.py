"""The folder's history (archive_history): how much waits uncommitted, and a manual commit.

The sidebar asks ``GET /api/history`` on every page and shows the count; its Commit button posts to
``/commit``. Everything else that commits does so at its own milestone, in the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

import archive_history
from api.deps import get_root
from api.schemas import CommitIn, CommitOut, HistoryOut

router = APIRouter(prefix="/api/history", tags=["history"])


def _out(root: Path) -> HistoryOut:
    try:
        status = archive_history.status(root)
    except archive_history.HistoryError as exc:
        return HistoryOut(repository=False, changed=0, last=None, problem=str(exc))
    last = status.last
    return HistoryOut(repository=status.repository, changed=status.changed, last=CommitOut(
        sha=last.sha, subject=last.subject, seconds_ago=round(archive_history.seconds_since(last))) if last else None)


@router.get("", response_model=HistoryOut)
def history_status(root: Path = Depends(get_root)):
    """Files changed since the last commit, and that commit."""
    return _out(root)


@router.post("/commit", response_model=HistoryOut)
def commit_now(body: CommitIn, root: Path = Depends(get_root)):
    """Commit everything uncommitted, under the given message (or a plain one)."""
    try:
        archive_history.commit(root, body.message.strip() or archive_history.MANUAL)
    except archive_history.HistoryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _out(root)
