"""Moving a document's pages around the archive, safely and in one place.

Every page on disk is an image plus a sidecar next to it (``data.sidecar_path_for``), and the archive
only sees a page that has both. Three features move pages — Receipt Detail's edit, Marked Workshop's
Accept and Toss, and Dedupe's Toss — so the rules live here rather than in each of them:

* a page keeps its image and sidecar together, and a failure never leaves one without the other;
* a document moves whole or not at all: if any of its files is open in another program (Windows won't
  move a file that is open without delete sharing), nothing moves, and a page that fails part-way puts
  the pages already moved back;
* a move never overwrites a file that is already there, whether or not the archive can see it;
* names are compared the way the file system compares them: without regard to case, so a rename that
  only changes letter case renames the files instead of mistaking them for the same name;
* the pages of one document are placed in scan order, so page 1 keeps the plain name and later pages
  get the " (2)" suffix;
* the numbers under one name run without gaps: when pages leave ``X``, ``X (2)``, ``X (3)``, the ones
  left are renumbered from the plain name up, so a later move never meets a gap it could fall into.

Files are moved with a plain rename, never copy-then-delete: within the archive a rename either happens
or doesn't, so a failure can't leave a second copy behind.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from data import delete_sidecar, read_sidecar, sidecar_path_for, write_sidecar
from models import ReviewDecision, Sidecar
from organize_utils import build_accepted_name
from viz_records import page_order

TOSSED = "tossed"
MARKED = "marked"


def load_pages(paths: list[Path]) -> list[tuple[Path, Sidecar]]:
    """Each page with its sidecar, in scan order. Raises LookupError for a page without one."""
    pages: list[tuple[Path, Sidecar]] = []
    for path in paths:
        sidecar = read_sidecar(path) if path.exists() else None
        if sidecar is None:
            raise LookupError(f"{path.name} has no sidecar; it can't be moved")
        pages.append((path, sidecar))
    pages.sort(key=lambda page: page_order(page[1], page[0].name))
    return pages


class FileInUse(OSError):
    """A file that has to move is open in another program, so nothing was moved."""


def _key(name: str) -> str:
    """A name as the file system compares it (NTFS ignores case)."""
    return name.casefold()


def same_name(a: Path, b: Path) -> bool:
    """Whether two paths name the same file, letter case aside."""
    return _key(str(a)) == _key(str(b))


def taken_stems(folder: Path, own: set[Path]) -> set[str]:
    """Names already used in ``folder`` (compared without case), ignoring these pages' own files.

    Counts images as well as sidecars: a scan whose sidecar is missing is invisible to the archive but
    would still be overwritten by a move.
    """
    if not folder.exists():
        return set()
    own_stems = {_key(path.stem) for path in own}
    return {_key(entry.stem) for entry in folder.iterdir() if entry.is_file() and _key(entry.stem) not in own_stems}


def free_name(folder: Path, base: str, suffix: str, taken: set[str]) -> Path:
    """``base`` in ``folder``, or ``base (2)``, ``base (3)``… if it is spoken for. Claims the name."""
    name = base
    index = 2
    while _key(name) in taken:
        name = f"{base} ({index})"
        index += 1
    taken.add(_key(name))
    return folder / f"{name}{suffix}"


def in_use(path: Path) -> bool:
    """Whether another program holds ``path`` open in a way that stops it being moved (Windows only).

    Asks Windows to open the file for deletion while sharing it with everyone: the open is refused
    exactly when some other handle doesn't allow the file to be moved or deleted. Nothing on disk
    changes. Elsewhere an open file can always be moved, so the answer is no.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    delete, share_all, open_existing = 0x00010000, 0x7, 3
    handle = kernel32.CreateFileW(str(path), delete, share_all, None, open_existing, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        sharing_violation = 32
        return ctypes.get_last_error() == sharing_violation
    kernel32.CloseHandle(handle)
    return False


def ensure_movable(pages: list[Path]) -> None:
    """Refuse (FileInUse) before anything moves if any page's image or sidecar is open in another
    program (a viewer, a sync client, a virus scan): moving it would fail part-way."""
    for page in pages:
        for path in (page, sidecar_path_for(page)):
            if path.exists() and in_use(path):
                raise FileInUse(f"{path.name} is open in another program; close it and try again.")


def move_page(source: Path, target: Path, sidecar: Sidecar) -> None:
    """Move one page to ``target`` and write its sidecar there, leaving nothing half-moved.

    The sidecar is written first and the image renamed second, so a failure leaves the page readable
    under one name or the other — never an image the archive can't see.
    """
    if str(target) == str(source):
        write_sidecar(target, sidecar)
        return
    if same_name(target, source):
        _rename_case(source, target, sidecar)
        return
    if target.exists() or sidecar_path_for(target).exists():
        raise FileExistsError(f"{target.name} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_sidecar(target, sidecar)
    try:
        os.rename(source, target)
    except OSError:
        delete_sidecar(target)
        raise
    delete_sidecar(source)


def _rename_case(source: Path, target: Path, sidecar: Sidecar) -> None:
    """The same name in different letter case: rename both files (the file system sees one name)."""
    old_sidecar, new_sidecar = sidecar_path_for(source), sidecar_path_for(target)
    if old_sidecar.exists():
        os.rename(old_sidecar, new_sidecar)
    try:
        os.rename(source, target)
    except OSError:
        if new_sidecar.exists():
            os.rename(new_sidecar, old_sidecar)
        raise
    write_sidecar(target, sidecar)


_NUMBERED = re.compile(r"^(?P<base>.*) \((?P<number>\d+)\)$")


def name_group(stem: str) -> tuple[str, int]:
    """``("X", 3)`` for ``X (3)``; ``("X", 1)`` for the plain ``X``."""
    match = _NUMBERED.match(stem)
    return (match["base"], int(match["number"])) if match else (stem, 1)


def close_gaps(folder: Path, base: str) -> list[tuple[str, str]]:
    """Renumber the pages named ``base``, ``base (2)``… in ``folder`` so the numbers run without gaps.

    Returns (old name, new name) for each page renamed. Pages keep their order, so a document's pages
    stay in scan order. A page open in another program leaves the numbers as they are.
    """
    if not folder.is_dir():
        return []
    members = sorted((number, path) for path in folder.iterdir()
                     if path.is_file() and path.suffix.lower() != ".json"
                     for group, number in [name_group(path.stem)] if _key(group) == _key(base))
    wanted = [(path, folder / f"{base if slot == 1 else f'{base} ({slot})'}{path.suffix}")
              for slot, (_number, path) in enumerate(members, start=1)]
    renames = [(path, target) for path, target in wanted if str(path) != str(target)]
    if not renames:
        return []
    try:
        ensure_movable([path for path, _ in renames])
    except FileInUse:
        return []
    done = []
    for path, target in renames:          # each moves down into a number already freed
        sidecar = read_sidecar(path)
        if sidecar is None:                # a scan the archive can't see keeps its name, and its place
            continue
        move_page(path, target, sidecar)
        done.append((path.name, target.name))
    return done


def _close_gaps_left_by(moved: list[tuple[Path, Path, Sidecar]]) -> None:
    """Renumber the name groups the moved pages left, in the folders they left."""
    left = {(source.parent, name_group(source.stem)[0]) for source, target, _ in moved
            if not (source.parent == target.parent and _key(name_group(source.stem)[0]) == _key(name_group(target.stem)[0]))}
    for folder, base in left:
        close_gaps(folder, base)


def _put_back(moved: list[tuple[Path, Path, Sidecar]]) -> None:
    """Undo the moves of a document that failed part-way, newest first."""
    for source, target, original in reversed(moved):
        if str(target) != str(source):
            move_page(target, source, original)
        else:
            write_sidecar(source, original)


def place_document(output_path: Path, pages: list[tuple[Path, Sidecar]], decision: ReviewDecision,
                   sidecar_for=None) -> list[str]:
    """File a document's pages where ``decision`` says they belong; returns the new relative paths.

    ``sidecar_for(sidecar)`` may rewrite each page's sidecar before it is written (the callers update
    the review, and sometimes the extraction, with what the user just entered). All pages move, or none.
    """
    ensure_movable([path for path, _ in pages])
    first = pages[0][1]
    folder, _base, _seconds = build_accepted_name(decision, first.original_filename)
    taken = taken_stems(output_path / folder, {path for path, _ in pages})

    placed: list[str] = []
    moved: list[tuple[Path, Path, Sidecar]] = []
    try:
        for path, sidecar in pages:
            page_folder, base, _ = build_accepted_name(decision, sidecar.original_filename)
            target = free_name(output_path / page_folder, base, path.suffix, taken)
            move_page(path, target, sidecar_for(sidecar) if sidecar_for else sidecar)
            moved.append((path, target, sidecar))
            placed.append(target.relative_to(output_path).as_posix())
    except OSError:
        _put_back(moved)
        raise
    _close_gaps_left_by(moved)
    return placed


def toss_document(output_path: Path, pages: list[Path]) -> list[str]:
    """Move a document's pages into ``tossed/`` under their original scan names.

    Two documents can share an original filename (different batches, same scanner counter), so a name
    already in ``tossed/`` is suffixed instead of overwritten.
    """
    folder = output_path / TOSSED
    loaded = load_pages(pages)
    ensure_movable([path for path, _ in loaded])
    taken = taken_stems(folder, {path for path, _ in loaded})
    tossed: list[str] = []
    moved: list[tuple[Path, Path, Sidecar]] = []
    try:
        for path, sidecar in loaded:
            original = Path(sidecar.original_filename or path.name)
            target = free_name(folder, original.stem, path.suffix, taken)
            move_page(path, target, sidecar.model_copy(update={"review": sidecar.review.model_copy(
                update={"verdict": "tossed"})}))
            moved.append((path, target, sidecar))
            tossed.append(target.relative_to(output_path).as_posix())
    except OSError:
        _put_back(moved)
        raise
    _close_gaps_left_by(moved)
    return tossed
