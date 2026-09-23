"""The archive's history: a git repository at the Papertrail folder's root, so nothing in it is ever lost.

The Papertrail folder (``settings.root_path``) holds the scan folder and the archive side by side, and its
root is a git repository: every scan as it came in and as it was turned or cut, every page and sidecar as
filed and as edited since, in every state they were ever in. The History page lists the commits and what
each changed; the standard tools (``git show``, ``git checkout``) bring any version back. Scans and their
archived copies are the same bytes, so git stores each image once however it moves.

A commit is made only at a **milestone**, and holds only what that milestone produced (``paths``), never
whatever else happens to be uncommitted:

* File Index confirm: the index and the new batch's scans.
* Finalize on Fix Rotation, Slice or Group: that step's files for every batch still being ingested (the
  scans as turned, the crops, the grouping and what it cleared). The pages apply each edit as it is made;
  Finalize is the one commit for the step, however many batches it covers.
* OCR, Parse ending, however they ended: that run's result files.
* Archive: the filed pages, the index, the match cache, the working files it deleted; then, once every
  filed page is in the last commit, the scans it removed from the scan folder.
* A manual commit, from the History page: everything uncommitted, with a message.
* The API stopping: everything uncommitted, as a parking commit (``PARKED``) that the next start undoes,
  so those changes go back to uncommitted and land in their proper milestone.

Nothing else commits. Review decisions, Workshop decisions, Receipt Detail edits, Normalize and Dedupe
accumulate, and the sidebar shows how many files wait, until a milestone that owns them or a manual commit.

The History page also takes things back, in two separate steps. :func:`uncommit` undoes the last
commit and leaves every file as it is, its changes uncommitted again (to commit differently, or to throw
away); it is refused when that would lose the only copy of a version, a file the commit holds having
changed or gone since. :func:`discard_changes` throws away uncommitted changes: a file goes back to its
last commit, and a file no commit holds is deleted.

A commit holds whole files: a milestone that owns ``decisions.json`` (Group's tosses) takes whatever else
in that file changed too (Review's decisions, say).

The repository is made on first use, with an identity of its own (the app makes the commits), line endings
left alone (a sidecar is committed byte for byte) and images kept out of delta compression (they don't
compress against each other, and trying costs minutes on a large archive). The temp files the app writes
through (``*.tmp``, ``*.partial``) and the embeddings cache are ignored. Every command names the root's
own ``.git`` and takes every path literally, so a root that sits inside some other repository never touches
that one, and a page called ``Foo [2]`` is one file, not a pattern.

Git has to be installed. Without it a milestone raises :class:`HistoryError`, and says so.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from settings import ARCHIVE_DIR, IMAGE_EXTENSIONS

GIT = "git"
IDENTITY = ("Papertrail", "papertrail@localhost")
#: The subject of the commit the API makes as it stops; the next start undoes a commit so named.
PARKED = "Parked at shutdown"
MANUAL = "Manual commit"

GITATTRIBUTES = "# written by papertrail (archive_history.py)\n* -text\n" + "".join(
    f"*{ext} -delta\n" for ext in sorted(IMAGE_EXTENSIONS))
GITIGNORE = f"""# written by papertrail (archive_history.py)
# files being written (data.atomic_write_text, Archive's copies, a scan turned or cut in place, the
# Workshop's treated copy)
*.tmp
*.partial
*.rotating.*
*.cutting.*
*.enhanced.png
# a cache of name embeddings, rebuilt from the names
{ARCHIVE_DIR}/name_embeddings.npz
# what Windows Explorer leaves behind
Thumbs.db
desktop.ini
"""

#: Windows: no console window for each git the API (running under pythonw) starts.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
#: One git at a time per process: the repository has one index, and two commits at once fight over its lock.
_lock = threading.RLock()


class HistoryError(RuntimeError):
    """Git couldn't record the folder; the message says why."""


class Refused(ValueError):
    """What was asked of the history would lose something, or no longer applies; the message says why."""


def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    """Run git on the root's own repository, never on one enclosing it, with every path taken literally."""
    command = [GIT, "-C", str(root), "--git-dir=.git", "--work-tree=.", "--literal-pathspecs", *args]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              input=stdin, creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise HistoryError("git isn't installed (or isn't on PATH), and the folder's history needs it.") from None
    if done.returncode != 0:
        raise HistoryError(f"git {args[0]} failed in {root}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def _pathspec(paths: Iterable[str]) -> str:
    """Paths for ``--pathspec-from-file=- --pathspec-file-nul``: relative to the root, forward slashes, each
    ended by a NUL, so no name is ever unquoted or split."""
    return "".join(Path(p).as_posix() + "\0" for p in paths)


def ensure_repository(root: Path) -> bool:
    """The root's repository, made if it isn't there yet (True when it was made just now)."""
    if (root / ".git").exists():
        return False
    root.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([GIT, "init", "--quiet", "--initial-branch=main", str(root)], check=True,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise HistoryError("git isn't installed (or isn't on PATH), and the folder's history needs it.") from None
    except subprocess.CalledProcessError as exc:
        raise HistoryError(f"git init failed in {root}: {exc.stderr.strip()}") from None
    # quotepath off: a page's name (2026年2月20日 ...) reads as itself in git's own output
    for key, value in (("user.name", IDENTITY[0]), ("user.email", IDENTITY[1]), ("core.autocrlf", "false"),
                       ("core.longpaths", "true"), ("core.untrackedCache", "true"), ("core.quotepath", "false")):
        _git(root, "config", key, value)
    for name, text in ((".gitattributes", GITATTRIBUTES), (".gitignore", GITIGNORE)):
        if not (root / name).exists():
            (root / name).write_text(text, encoding="utf-8")
    # its own settings are its first commit, so no milestone leaves them behind as untracked files
    _git(root, "add", "--", ".gitattributes", ".gitignore")
    _git(root, "commit", "--quiet", "--message", "History started", "--", ".gitattributes", ".gitignore")
    return True


def keep_blob(root: Path, path: Path) -> str:
    """Put a file's bytes in the repository's objects as they are now, without committing anything, and
    return their id: a scan about to be rewritten keeps its old bytes this way (``read_blob`` brings them
    back). Unreachable objects are kept for good (``gc.pruneExpire never``), so no gc takes one away."""
    with _lock:
        _git(root, "config", "gc.pruneExpire", "never")
        return _git(root, "hash-object", "-w", "--", str(path.resolve())).strip()


def read_blob(root: Path, blob: str) -> bytes:
    """The bytes ``keep_blob`` kept."""
    command = [GIT, "-C", str(root), "--git-dir=.git", "cat-file", "blob", blob]
    try:
        done = subprocess.run(command, capture_output=True, creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise HistoryError("git isn't installed (or isn't on PATH), and the folder's history needs it.") from None
    if done.returncode != 0:
        raise HistoryError(f"git cat-file failed in {root}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


Kind = Literal["added", "modified", "deleted"]


@dataclass(frozen=True)
class Change:
    """A file a commit changed, or that differs from the last commit: relative to the root."""
    path: str
    kind: Kind
    #: lines added and removed, where they were counted (:func:`_counted`); None for a binary file
    lines: tuple[int, int] | None = None


def _kind(code: str) -> Kind:
    """What a status letter (``git status``'s two, or ``--name-status``'s one) says happened to a file."""
    if "D" in code:
        return "deleted"
    return "added" if code == "??" or code.startswith("A") else "modified"


def _changes(root: Path, paths: Iterable[str] | None = None) -> list[Change]:
    """The files that differ from the last commit (changed, added, deleted; ignored files aside), under
    ``paths`` if given: each a file, or a folder and everything in it. A path that matches nothing is
    simply nothing. (``git status`` takes no pathspec file, so the whole tree is asked and filtered here.)"""
    entries = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    changed: list[Change] = []
    skip = False
    for entry in entries:
        if skip:                        # the source of a rename or copy, which follows its own entry
            skip = False
            continue
        if not entry:
            continue
        changed.append(Change(path=entry[3:], kind=_kind(entry[:2])))
        skip = entry[0] in "RC"
    if paths is None:
        return changed
    wanted = {Path(p).as_posix().rstrip("/") for p in paths}
    return [c for c in changed if c.path in wanted or any(c.path.startswith(w + "/") for w in wanted)]


def _status(root: Path, paths: Iterable[str] | None = None) -> list[str]:
    """The paths of :func:`_changes`."""
    return [c.path for c in _changes(root, paths)]


def commit(root: Path, message: str, paths: Iterable[str] | None = None) -> str | None:
    """Commit what changed under ``paths`` (everything, when None); the commit's short id, or None when
    nothing under them had changed. Paths are relative to the root."""
    with _lock:
        ensure_repository(root)
        changed = _status(root, paths)
        if not changed:
            return None
        _git(root, "add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul", stdin=_pathspec(changed))
        _git(root, "commit", "--quiet", "--message", message, "--pathspec-from-file=-", "--pathspec-file-nul",
             stdin=_pathspec(changed))
        return _git(root, "rev-parse", "--short", "HEAD").strip()


def changed_paths(root: Path, under: Iterable[str] | None = None) -> set[str] | None:
    """Paths (relative to the root) that differ from the last commit, under ``under`` (each a file, or a
    folder and everything in it) if given; None before the repository is made (asking never makes it)."""
    if not (root / ".git").exists():
        return None
    with _lock:
        return set(_status(root, under))


def tracked_paths(root: Path, under: str | None = None) -> set[str]:
    """Paths (relative to the root) the last commit holds, under ``under`` if given."""
    with _lock:
        if not (root / ".git").exists():
            return set()
        args = ["ls-files", "-z"]
        if under:
            args += ["--", under]
        return {p for p in _git(root, *args).split("\0") if p}


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    at: float          # seconds since the epoch


def head(root: Path) -> Commit | None:
    """The last commit, or None before the first one."""
    if not (root / ".git").exists():
        return None
    try:
        line = _git(root, "log", "-1", "--format=%h%x00%s%x00%ct").strip()
    except HistoryError:            # a repository with no commit yet
        return None
    sha, subject, at = line.split("\0")
    return Commit(sha=sha, subject=subject, at=float(at))


@dataclass(frozen=True)
class Status:
    repository: bool        # whether the root has a repository yet
    changed: int            # files that differ from the last commit
    last: Commit | None


def status(root: Path) -> Status:
    """How the root stands against its history, for the sidebar."""
    if not (root / ".git").exists():
        return Status(repository=False, changed=0, last=None)
    with _lock:
        return Status(repository=True, changed=len(_status(root)), last=head(root))


# --- looking back, and taking back ----------------------------------------------------------------------------
def _binary_by_name(path: str) -> bool:
    """An image, which has no lines; told by its name, so counting never reads one (they are large)."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _numstat(root: Path, command: tuple[str, ...], paths: list[str]) -> dict[str, tuple[int, int] | None]:
    """Lines added and removed per path by a ``--numstat`` diff (None: binary), asked of these paths only, a
    command line's worth at a time (Windows caps one at 32k characters)."""
    counted: dict[str, tuple[int, int] | None] = {}

    def ask(chunk: list[str]) -> None:
        for entry in _git(root, *command, "--no-renames", "--numstat", "-z", "--", *chunk).split("\0"):
            if entry:
                added, removed, name = entry.split("\t", 2)
                counted[name] = None if added == "-" else (int(added), int(removed))

    chunk: list[str] = []
    size = 0
    for path in paths:
        if chunk and size + len(path) + 3 > 20_000:
            ask(chunk)
            chunk, size = [], 0
        chunk.append(path)
        size += len(path) + 3               # a space and the quotes a name with a space in it gets
    if chunk:
        ask(chunk)
    return counted


def _file_lines(path: Path) -> tuple[int, int] | None:
    """A file as it is on disk, all its lines added, counted as git counts them (None if it looks binary);
    none at all when it isn't there."""
    if not path.is_file():
        return 0, 0
    data = path.read_bytes()
    if b"\0" in data[:8000]:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0), 0


def _counted(root: Path, found: list[Change], command: tuple[str, ...], diffed: bool = True) -> list[Change]:
    """``found`` with each text file's lines added and removed, by ``command`` (a diff of what they changed).
    With ``diffed`` False an added file isn't asked of the diff: it is one git doesn't know of yet, which no
    diff counts, so it is counted from the file instead."""
    text = [c.path for c in found if not _binary_by_name(c.path) and (diffed or c.kind != "added")]
    counted = _numstat(root, command, text) if text else {}
    out = []
    for c in found:
        if _binary_by_name(c.path):
            out.append(c)
        elif c.path in counted:
            out.append(replace(c, lines=counted[c.path]))
        else:
            out.append(replace(c, lines=_file_lines(root / c.path)))
    return out


def changes(root: Path) -> list[Change]:
    """The files that differ from the last commit, by path, with the lines changed in each; none before the
    repository is made."""
    if not (root / ".git").exists():
        return []
    with _lock:
        return _counted(root, sorted(_changes(root), key=lambda c: c.path), ("diff", "HEAD"), diffed=False)


@dataclass(frozen=True)
class LoggedCommit(Commit):
    files: int          # files it changed
    first: bool         # the history's first commit, which every other builds on


def log(root: Path, skip: int = 0, limit: int = 50) -> tuple[list[LoggedCommit], bool]:
    """Commits newest first, ``limit`` of them after the newest ``skip``, and whether there are older ones.
    Counting their files names them only, without reading a byte of any, so a commit of every scan is quick."""
    if head(root) is None:
        return [], False
    out = _git(root, "log", "--no-renames", "--name-status", "-z", f"--skip={skip}", f"--max-count={limit + 1}",
               "--format=%x01%h%x00%s%x00%ct%x00%P")
    commits = []
    for record in out.split("\x01")[1:]:
        sha, subject, at, parents, *files = record.split("\0")
        # the files follow as a status and a path each, the first after a line break
        named = [f for f in files if f.strip("\n")]
        commits.append(LoggedCommit(sha=sha, subject=subject, at=float(at), files=len(named) // 2, first=not parents))
    return commits[:limit], len(commits) > limit


def _commit_changes(root: Path, rev: str) -> list[Change]:
    out = _git(root, "diff-tree", "--no-commit-id", "--root", "-r", "--no-renames", "--name-status", "-z", rev).split("\0")
    return [Change(path=path, kind=_kind(code)) for code, path in zip(out[0::2], out[1::2], strict=False) if path]


def commit_files(root: Path, sha: str) -> list[Change] | None:
    """The files a commit changed, by path, with the lines changed in each; None when there is no such commit."""
    if not all(c in "0123456789abcdef" for c in sha) or not 4 <= len(sha) <= 40 or not (root / ".git").exists():
        return None
    with _lock:
        try:
            _git(root, "cat-file", "-e", f"{sha}^{{commit}}")
        except HistoryError:
            return None
        return _counted(root, _commit_changes(root, sha), ("diff-tree", "--no-commit-id", "--root", "-r", sha))


def uncommit(root: Path, sha: str) -> Commit:
    """Undo the last commit, which must be ``sha`` (so a milestone that committed since isn't the one undone),
    leaving every file as it is: its changes are uncommitted again. Refused for the first commit, and when a
    file the commit holds has changed or gone since, as that version would then be nowhere."""
    with _lock:
        last = head(root)
        if last is None or len(sha) < 4 or not _git(root, "rev-parse", "HEAD").strip().startswith(sha):
            now = f": the last is now “{last.subject}”" if last else ""
            raise Refused(f"That is no longer the last commit{now}.")
        if not _git(root, "log", "-1", "--format=%P").strip():
            raise Refused("The history's first commit can't be uncommitted: every other commit builds on it.")
        held = {c.path for c in _commit_changes(root, "HEAD") if c.kind != "deleted"}
        lost = sorted(held & set(_status(root)))
        if lost:
            some = ", ".join(lost[:3]) + (f" and {len(lost) - 3} more" if len(lost) > 3 else "")
            raise Refused(f"{len(lost)} file{'s' if len(lost) != 1 else ''} this commit holds changed or went since "
                          f"({some}), and the commit holds the only copy of what they were. Discard those changes "
                          "first to bring that back.")
        _git(root, "reset", "--quiet", "--mixed", "HEAD~1")
        return last


def discard_changes(root: Path, paths: Iterable[str]) -> int:
    """Throw away the uncommitted changes to these files (exact paths; one that no longer differs is skipped):
    one the last commit holds goes back to it, deleted or not, and one it doesn't hold is deleted, and taken
    out of the index too if a commit that failed part-way had added it there. How many were thrown away."""
    if not (root / ".git").exists():
        raise Refused("The folder has no history yet, so there is nothing to go back to.")
    wanted = set(paths)
    with _lock:
        thrown = [c.path for c in _changes(root) if c.path in wanted]
        if not thrown:
            return 0
        # which of them the last commit holds: asked of it by name, one per line, so any number at once
        kinds = _git(root, "cat-file", "--batch-check=%(objecttype)",
                     stdin="".join(f"HEAD:{p}\n" for p in thrown)).splitlines()
        held = [p for p, kind in zip(thrown, kinds, strict=True) if kind == "blob"]
        new = [p for p, kind in zip(thrown, kinds, strict=True) if kind != "blob"]
        if held:
            _git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--pathspec-from-file=-",
                 "--pathspec-file-nul", stdin=_pathspec(held))
        if new:
            # forced: its staged content, in no commit, is what is being thrown away
            _git(root, "rm", "--cached", "--force", "--quiet", "--ignore-unmatch", "--pathspec-from-file=-",
                 "--pathspec-file-nul", stdin=_pathspec(new))
            for path in new:
                (root / path).unlink(missing_ok=True)
        return len(thrown)


# --- parking at shutdown ------------------------------------------------------------------------------------
def park(root: Path) -> str | None:
    """Commit everything uncommitted as the API stops, so nothing is lost while it is down; undone by
    :func:`unpark` at the next start. Nothing is made when nothing had changed, or when the folder has no
    history yet: its first milestone starts one, not a shutdown that might be cut short."""
    if not (root / ".git").exists():
        return None
    return commit(root, PARKED)


def unpark(root: Path) -> bool:
    """Undo a parking commit left by the last shutdown, so its changes are uncommitted again and land in
    their proper milestone. True when there was one."""
    last = head(root)
    if last is None or last.subject != PARKED:
        return False
    with _lock:                 # never the first commit: "History started" is (ensure_repository)
        _git(root, "reset", "--quiet", "--mixed", "HEAD~1")
    return True


def seconds_since(commit_: Commit, now: float | None = None) -> float:
    return (time.time() if now is None else now) - commit_.at
