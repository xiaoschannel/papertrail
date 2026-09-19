"""Moving a document's pages around the archive, safely and in one place.

Every page on disk is an image plus a sidecar next to it (``data.sidecar_path_for``), and the archive
only sees a page that has both. Three features move pages — Receipt Detail's edit, Marked Workshop's
Accept and Toss, and Dedupe's Toss — so the rules live here rather than in each of them:

* a page keeps its image and sidecar together, and a failure never leaves one without the other;
* a move never overwrites a file that is already there, whether or not the archive can see it;
* the pages of one document are placed in scan order, so page 1 keeps the plain name and later pages
  get the " (2)" suffix.
"""

from __future__ import annotations

import shutil
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


def taken_stems(folder: Path, own: set[Path]) -> set[str]:
    """Names already used in ``folder``, ignoring these pages' own files.

    Counts images as well as sidecars: a scan whose sidecar is missing is invisible to the archive but
    would still be overwritten by a move.
    """
    if not folder.exists():
        return set()
    own_stems = {path.stem for path in own}
    return {entry.stem for entry in folder.iterdir() if entry.is_file() and entry.stem not in own_stems}


def free_name(folder: Path, base: str, suffix: str, taken: set[str]) -> Path:
    """``base`` in ``folder``, or ``base (2)``, ``base (3)``… if it is spoken for. Claims the name."""
    name = base
    index = 2
    while name in taken:
        name = f"{base} ({index})"
        index += 1
    taken.add(name)
    return folder / f"{name}{suffix}"


def move_page(source: Path, target: Path, sidecar: Sidecar) -> None:
    """Move one page to ``target`` and write its sidecar there, leaving nothing half-moved.

    The sidecar is written first and the image moved second, so a failure leaves the page readable
    under one name or the other — never an image the archive can't see.
    """
    if target == source:
        write_sidecar(target, sidecar)
        return
    if target.exists() or sidecar_path_for(target).exists():
        raise FileExistsError(f"{target.name} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_sidecar(target, sidecar)
    try:
        shutil.move(str(source), str(target))
    except Exception:
        delete_sidecar(target)
        raise
    delete_sidecar(source)


def place_document(output_path: Path, pages: list[tuple[Path, Sidecar]], decision: ReviewDecision,
                   sidecar_for=None) -> list[str]:
    """File a document's pages where ``decision`` says they belong; returns the new relative paths.

    ``sidecar_for(sidecar)`` may rewrite each page's sidecar before it is written (the callers update
    the review, and sometimes the extraction, with what the user just entered).
    """
    first = pages[0][1]
    folder, _base, _seconds = build_accepted_name(decision, first.original_filename)
    taken = taken_stems(output_path / folder, {path for path, _ in pages})

    placed: list[str] = []
    for path, sidecar in pages:
        page_folder, base, _ = build_accepted_name(decision, sidecar.original_filename)
        target = free_name(output_path / page_folder, base, path.suffix, taken)
        move_page(path, target, sidecar_for(sidecar) if sidecar_for else sidecar)
        placed.append(target.relative_to(output_path).as_posix())
    return placed


def toss_document(output_path: Path, pages: list[Path]) -> list[str]:
    """Move a document's pages into ``tossed/`` under their original scan names.

    Two documents can share an original filename (different batches, same scanner counter), so a name
    already in ``tossed/`` is suffixed instead of overwritten.
    """
    folder = output_path / TOSSED
    loaded = load_pages(pages)
    taken = taken_stems(folder, {path for path, _ in loaded})
    tossed: list[str] = []
    for path, sidecar in loaded:
        original = Path(sidecar.original_filename or path.name)
        target = free_name(folder, original.stem, path.suffix, taken)
        move_page(path, target, sidecar.model_copy(update={"review": sidecar.review.model_copy(
            update={"verdict": "tossed"})}))
        tossed.append(target.relative_to(output_path).as_posix())
    return tossed
