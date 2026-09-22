"""One-off migration: put archived multi-page documents in the page order they were grouped in.

Before sidecars recorded a page's place in its document (``Sidecar.page``), Archive filed every
document's pages in scan order, and everything reading the archive showed them that way, so a group
whose pages were swapped in Group lost its order when filed. ``documents.json`` still holds each group
in the order it was grouped, so the order can be put back: each page's sidecar gets its ``page``, and a
filed document's names ("X", "X (2)"…) go to its pages in page order (the same names, handed round).
Pages in marked/ and tossed/ keep their scan's name, so only their sidecars change.

Where a group was saved in the wrong order, the page lets its pages be swapped first: the order chosen
is recorded in documents.json too (Undo puts the group back).

Every page of a document is copied under ``.page-order-backup/`` before anything changes, and Undo
puts them all back. Redo puts every document done so far back from its backup and does it again with
the code as it is now, so a fix to the migration reaches what was already done. Finalize deletes the
backups once the result has been checked; the migration isn't done until it has.

Temporary: kept as a git tag, not part of the shipped fix.
"""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from data import (
    _iter_year_month_dirs, load_document_groups, read_sidecar, save_document_groups, sidecar_path_for, write_sidecar,
)
from document_files import MARKED, TOSSED, ensure_movable, move_page, name_group
from models import DocumentKey
from settings import IMAGE_EXTENSIONS

BACKUP_DIR = ".page-order-backup"
_MANIFEST = "manifest.json"
#: The name a page waits under while the document's names are handed round.
_WAITING = ".page-order"


class Page(BaseModel):
    #: where the page is now, under the output folder
    rel_path: str
    serial: int
    #: its place in its document, from 1
    page: int
    #: where it goes (the same as ``rel_path`` when only its sidecar changes)
    target: str


class Document(BaseModel):
    key: str
    #: in page order
    pages: list[Page]
    #: a filed document's names in their numbered order ("X", "X (2)"…), handed to its pages in page order;
    #: empty for one in marked/ or tossed/, whose pages keep their names
    slots: list[str] = []
    #: in a backup's manifest: the group in documents.json before a page order was chosen here, if one was
    group_before: list[str] | None = None

    @property
    def renames(self) -> bool:
        return any(p.rel_path != p.target for p in self.pages)


class Plan(BaseModel):
    documents: list[Document]
    #: documents that can't be put in order here, and why
    problems: list[str]


@dataclass(frozen=True)
class _Filed:
    """What the migration needs from an archived page's sidecar."""
    path: Path
    serial: int
    document_key: str | None
    page: int


#: Each archive folder's pages as last read, with the folder's modified time then: reading all the archive's
#: sidecars takes seconds, and a folder's time moves on whenever a page in it is written.
_folders: dict[Path, tuple[int, list[tuple[int, _Filed]]]] = {}
_folders_lock = threading.Lock()


def _read_folder(folder: Path) -> list[tuple[int, _Filed]]:
    pages = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS or path.name.endswith(".enhanced.png"):
            continue
        sidecar = sidecar_path_for(path)
        if not sidecar.is_file():
            continue
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        if raw.get("batch_id") is None or raw.get("serial") is None:
            continue
        pages.append((raw["batch_id"], _Filed(path, raw["serial"], raw.get("document_key"), raw.get("page", 1))))
    return pages


def _archived_pages(output_path: Path) -> dict[tuple[int, int], list[_Filed]]:
    """Every archived page with a sidecar, by (batch, serial)."""
    folders = sorted(_iter_year_month_dirs(output_path)) + [output_path / MARKED, output_path / TOSSED]
    pages: dict[tuple[int, int], list[_Filed]] = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        modified = folder.stat().st_mtime_ns
        with _folders_lock:
            cached = _folders.get(folder)
        if cached is None or cached[0] != modified:
            cached = (modified, _read_folder(folder))
            with _folders_lock:
                _folders[folder] = cached
        for batch_id, filed in cached[1]:
            pages.setdefault((batch_id, filed.serial), []).append(filed)
    return pages


def _forget(paths: list[Path]) -> None:
    """Read these pages' folders afresh next time, whatever their times say."""
    with _folders_lock:
        for path in paths:
            _folders.pop(path.parent, None)


def _rel(output_path: Path, path: Path) -> str:
    return path.relative_to(output_path).as_posix()


def _document(output_path: Path, group: list[str], archived: dict) -> Document | str | None:
    """What putting one group's archived pages in order takes: a Document, a problem, or None (nothing
    to do: already in order, or not archived)."""
    key = str(DocumentKey.from_group(group))
    parsed = [DocumentKey.parse(k) for k in group]
    found = [archived.get((p.batch_id, p.first_serial), []) for p in parsed]
    if not any(found):
        return None                                   # not archived yet: Archive files it in order now
    if any(len(f) != 1 for f in found):
        return f"{key}: {sum(map(len, found))} archived pages for its {len(group)} scans"
    pages = [f[0] for f in found]
    if any(filed.document_key != key for filed in pages):
        return f"{key}: its archived pages aren't filed as this document"
    if all(filed.page == i for i, filed in enumerate(pages, start=1)):
        return None
    folders = {filed.path.parent for filed in pages}
    bases = {name_group(filed.path.stem)[0].casefold() for filed in pages}
    filed_away = len(folders) == 1 and next(iter(folders)).name not in (MARKED, TOSSED)
    if filed_away and len(bases) != 1:
        return f"{key}: its pages are filed under different names"
    # A filed document's names go to its pages in page order; marked and tossed pages keep theirs.
    slots = sorted((filed.path for filed in pages), key=lambda p: name_group(p.stem)[1]) if filed_away else []
    targets = slots or [filed.path for filed in pages]
    return Document(key=key, slots=[_rel(output_path, s) for s in slots], pages=[
        Page(rel_path=_rel(output_path, filed.path), serial=filed.serial, page=i, target=_rel(output_path, target))
        for i, (filed, target) in enumerate(zip(pages, targets), start=1)])


def _by_scan(key: str) -> tuple[int, int]:
    parsed = DocumentKey.parse(key)
    return parsed.batch_id, parsed.first_serial


def plan(output_path: Path) -> Plan:
    """The archived multi-page documents whose sidecars don't have the grouped page order yet."""
    archived = _archived_pages(output_path)
    documents, problems = [], []
    for group in load_document_groups(output_path).groups:
        if len(group) < 2:
            continue
        result = _document(output_path, group, archived)
        if isinstance(result, Document):
            documents.append(result)
        elif result is not None:
            problems.append(result)
    documents.sort(key=lambda d: _by_scan(d.key))
    return Plan(documents=documents, problems=problems)


def _backup_dir(output_path: Path, key: str) -> Path:
    return output_path / BACKUP_DIR / key.replace(":", "_")


def _waiting(path: Path) -> Path:
    return path.with_name(f"{path.stem}{_WAITING}{path.suffix}")


def _planned(output_path: Path, key: str) -> Document:
    document = next((d for d in plan(output_path).documents if d.key == key), None)
    if document is None:
        raise KeyError(f"no archived document {key} to put in order")
    return document


def _set_group(output_path: Path, key: str, group: list[str]) -> None:
    """Record a document's pages in documents.json in this order."""
    groups = load_document_groups(output_path)
    groups.groups = [group if len(g) > 1 and str(DocumentKey.from_group(g)) == key else g for g in groups.groups]
    save_document_groups(output_path, groups)


def put_in_order(output_path: Path, key: str, order: list[int] | None = None) -> None:
    """Put one archived document's pages in page order, after backing every page up.

    ``order`` (its scans' serials, page 1 first) is the order to use instead of the grouped one, for a group
    saved in the wrong order; it is recorded in documents.json, and Undo puts the group back.
    """
    document = _planned(output_path, key)
    group_before = None
    if order is not None and order != [p.serial for p in document.pages]:
        if sorted(order) != sorted(p.serial for p in document.pages):
            raise ValueError(f"{order} aren't the scans of {key}")
        batch_id = DocumentKey.parse(key).batch_id
        group_before = [f"{batch_id}:{p.serial}" for p in document.pages]
        _set_group(output_path, key, [f"{batch_id}:{serial}" for serial in order])
        try:
            document = _planned(output_path, key)
        except KeyError:            # its pages are in that order already: nothing to do, nothing to record
            _set_group(output_path, key, group_before)
            raise ValueError(f"{key}'s pages are in that order already") from None
    paths = [output_path / p.rel_path for p in document.pages]
    ensure_movable(paths)
    backup = _backup_dir(output_path, key)
    if backup.exists():
        raise ValueError(f"{key} has a backup already; undo or finalize it first")
    for page, path in zip(document.pages, paths):
        copy = backup / page.rel_path
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar_path_for(path), sidecar_path_for(copy))
        shutil.copy2(path, copy)
    _apply(output_path, backup, document.model_copy(update={"group_before": group_before}))


def _apply(output_path: Path, backup: Path, document: Document) -> None:
    """Record what is done in the backup's manifest, then do it."""
    (backup / _MANIFEST).write_text(document.model_dump_json(indent=2), encoding="utf-8")
    paths = [output_path / p.rel_path for p in document.pages]
    try:
        # Pages that change names wait under a name no other page has, so no move lands on a page not yet moved.
        waiting = []
        for page, path in zip(document.pages, paths):
            sidecar = read_sidecar(path).model_copy(update={"page": page.page})
            if page.target == page.rel_path:
                write_sidecar(path, sidecar)
            else:
                move_page(path, _waiting(path), sidecar)
                waiting.append((_waiting(path), output_path / page.target, sidecar))
        for source, target, sidecar in waiting:
            move_page(source, target, sidecar)
    finally:
        _forget(paths)


def _manifest(backup: Path) -> Document:
    return Document.model_validate_json((backup / _MANIFEST).read_text(encoding="utf-8"))


def put_in_order_documents(output_path: Path) -> list[Document]:
    """The documents put in order here that can still be put back, as they were planned."""
    root = output_path / BACKUP_DIR
    if not root.is_dir():
        return []
    documents = [_manifest(d) for d in root.iterdir() if (d / _MANIFEST).is_file()]
    return sorted(documents, key=lambda d: _by_scan(d.key))


def before(output_path: Path, document: Document) -> list[str]:
    """A document put in order here as it was filed before, from its backup: the backed-up pages, under the
    output folder, in the order the archive showed them (by name, then scan)."""
    backup = _backup_dir(output_path, document.key)
    pages = sorted(document.pages, key=lambda p: (name_group(Path(p.rel_path).stem)[1], p.serial))
    return [_rel(output_path, backup / p.rel_path) for p in pages]


def undo(output_path: Path, key: str) -> None:
    """Put a document's pages back as they were (names and sidecars, and its group if a page order was chosen
    here), and drop its backup."""
    document = _restore(output_path, key)
    if document.group_before is not None:
        _set_group(output_path, key, document.group_before)
    shutil.rmtree(_backup_dir(output_path, key))


def redo_all(output_path: Path) -> int:
    """Put every document done here back from its backup and do it again, with the code as it is now (the
    backup stays: it is still the archive as it was; a page order chosen here stays chosen). Returns how many
    were redone."""
    documents = put_in_order_documents(output_path)
    for document in documents:
        _restore(output_path, document.key)
        again = _planned(output_path, document.key).model_copy(update={"group_before": document.group_before})
        _apply(output_path, _backup_dir(output_path, document.key), again)
    return len(documents)


def _restore(output_path: Path, key: str) -> Document:
    """Put a document's pages back from its backup, as they were (names and sidecars); returns its manifest."""
    backup = _backup_dir(output_path, key)
    if not (backup / _MANIFEST).is_file():
        raise KeyError(f"no backup of {key}")
    document = _manifest(backup)
    paths = [output_path / p.rel_path for p in document.pages]
    # Its pages hold the same names as before, handed round; one gone means it was changed since.
    for page, path in zip(document.pages, paths):
        if not ((output_path / page.target).is_file() or _waiting(path).is_file()):
            raise ValueError(f"{page.target} isn't there any more; {key} was changed since it was put in order")
    ensure_movable(paths)
    for path in paths:          # an interrupted run can leave pages waiting
        _waiting(path).unlink(missing_ok=True)
        sidecar_path_for(_waiting(path)).unlink(missing_ok=True)
    for page, path in zip(document.pages, paths):
        copy = backup / page.rel_path
        for source, target in ((sidecar_path_for(copy), sidecar_path_for(path)), (copy, path)):
            tmp = target.with_name(f"{target.name}.restoring")
            shutil.copy2(source, tmp)
            tmp.replace(target)
    _forget(paths)
    return document


def finalize(output_path: Path) -> int:
    """Delete every backup, once the documents have been checked: they can't be undone after.
    Returns how many documents' backups went."""
    count = len(put_in_order_documents(output_path))
    root = output_path / BACKUP_DIR
    if root.is_dir():
        shutil.rmtree(root)
    return count
