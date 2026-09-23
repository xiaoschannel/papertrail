"""The folder's history (archive_history), for the History page and the sidebar's count.

The sidebar asks ``GET /api/history`` on every page and shows the count. The History page lists what is
uncommitted and the commits, commits by hand, uncommits the last commit (keeping its files) and throws
uncommitted changes away. Everything else that commits does so at its own milestone, in the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

import archive_history
from api.deps import get_root
from api.guards import no_job_running
from api.schemas import (ChangeOut, CommitIn, CommitOut, CommitsOut, DiscardChangesIn, HistoryOut, LoggedCommitOut,
                         UncommitIn)

router = APIRouter(prefix="/api/history", tags=["history"])


def _out(root: Path) -> HistoryOut:
    try:
        status = archive_history.status(root)
    except archive_history.HistoryError as exc:
        return HistoryOut(repository=False, changed=0, last=None, problem=str(exc))
    last = status.last
    return HistoryOut(repository=status.repository, changed=status.changed, last=CommitOut(
        sha=last.sha, subject=last.subject, seconds_ago=round(archive_history.seconds_since(last))) if last else None)


def _changes(found: list[archive_history.Change]) -> list[ChangeOut]:
    return [ChangeOut(path=c.path, kind=c.kind) for c in found]


@router.get("", response_model=HistoryOut)
def history_status(root: Path = Depends(get_root)):
    """Files changed since the last commit, and that commit."""
    return _out(root)


@router.get("/changes", response_model=list[ChangeOut])
def uncommitted(root: Path = Depends(get_root)):
    """The files that differ from the last commit, by path."""
    return _changes(archive_history.changes(root))


@router.get("/commits", response_model=CommitsOut)
def commits(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500), root: Path = Depends(get_root)):
    """Commits newest first, and whether older ones follow."""
    found, more = archive_history.log(root, skip, limit)
    return CommitsOut(more=more, commits=[LoggedCommitOut(
        sha=c.sha, subject=c.subject, seconds_ago=round(archive_history.seconds_since(c)), files=c.files,
        first=c.first) for c in found])


@router.get("/commits/{sha}", response_model=list[ChangeOut])
def commit_files(sha: str, root: Path = Depends(get_root)):
    """The files a commit changed, by path."""
    found = archive_history.commit_files(root, sha)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No commit {sha} in the folder's history.")
    return _changes(found)


@router.post("/commit", response_model=HistoryOut)
def commit_now(body: CommitIn, root: Path = Depends(get_root)):
    """Commit everything uncommitted, under the given message (or a plain one)."""
    archive_history.commit(root, body.message.strip() or archive_history.MANUAL)
    return _out(root)


@router.post("/uncommit", response_model=HistoryOut)
def uncommit(body: UncommitIn, root: Path = Depends(get_root)):
    """Undo the last commit, leaving its files as they are, uncommitted again."""
    try:
        archive_history.uncommit(root, body.sha)
    except archive_history.Refused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(root)


@router.post("/discard-changes", response_model=HistoryOut)
def discard_changes(body: DiscardChangesIn, root: Path = Depends(get_root)):
    """Throw away these files' uncommitted changes. Not while a job runs: it may be writing them."""
    with no_job_running("discard changes"):
        try:
            archive_history.discard_changes(root, body.paths)
        except archive_history.Refused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(root)
