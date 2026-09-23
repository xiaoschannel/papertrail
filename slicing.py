"""Cutting a scanned sheet of small receipts into one page per receipt (the Slice step of File Index).

Tickets too small to scan on their own (食券 and the like) are taped onto a sheet in an m x n grid and
scanned together. Slicing the sheet puts a ``SheetGrid`` on it; each filled cell becomes a crop, a page of
its own that OCR, Parse, Review and Archive handle like any other.

The rules slicing keeps:

* **Serials run without gaps.** A batch is its scanned pages, then its crops from ``max(scan serial) + 1``:
  sheets in serial order and, within a sheet, filled cells in reading order. Changing one sheet's grid can
  therefore move the crops after it to other serials. A crop whose serial now names something else loses
  its mid-ingest results (OCR, extraction, decision): they were about a different ticket. ``plan_slices``
  says exactly which before ``apply_slices`` does it. Slicing sheets in scan order never moves anything.
* **Nothing is lost.** The sheet stays in the input folder and in the batch, tossed with
  ``toss_reason="sliced"``, so Archive files it under ``tossed/``. Only slicing sets or clears that toss.
* **Crops inherit the sheet's rotation**, so a sheet is turned upright before it is sliced; neither a
  sliced sheet nor a crop can be rotated.
* **A trim belongs to what it was drawn on.** A crop whose serial moves loses its trim with its other
  results, and a sheet's trim goes when it is sliced: its crops are what is read, each trimmed on its own.
* Crops are files under ``<input>/slices/``, named for their sheet, cell and box (never their serial, so
  moving a crop never renames its file, and a cell cut differently gets a file of its own). The input
  folder's own listing only reads its top level, so a crop is never offered as a new scan.

``apply_slices`` writes in an order a crash can't hurt. Crops are cut first: a missing or unreadable sheet
fails there, before anything is changed, and since a name says exactly what was cut, the files
``batches.json`` still refers to keep meaning what it says. Results are dropped next (the worst case is OCR
to redo), then ``batches.json`` is written in one atomic write, and unused crops are swept last. A crash
after the results are dropped but before the batch is written leaves the sheet's toss and grid
disagreeing, which ``mismatches`` reports and saving or unslicing the sheet repairs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from data import (
    EXTRACTION_RUNS,
    OCR_RUNS,
    drop_extractions,
    drop_model_runs,
    drop_trims,
    load_decisions,
    load_document_groups,
    load_extractions,
    load_ocr_batch,
    load_trims,
    save_decisions,
    save_ocr_batch,
    save_scan_index,
)
from models import (
    GRID_SCALE,
    MAX_GRID,
    Box,
    ReviewDecision,
    ScanBatch,
    ScanIndex,
    SheetGrid,
    SliceOf,
    batch_serial_key,
    load_scan_index,
)

#: Where crops are written, inside the input folder.
SLICES_DIR = "slices"


class SliceRefused(ValueError):
    """The sheet can't be sliced (or unsliced) as asked; the message says why."""


class StaleSlicePlan(Exception):
    """The batch changed since the plan was shown."""


# =====================================================================================
# The grid
# =====================================================================================
def validate_grid(grid: SheetGrid) -> None:
    """Raise SliceRefused unless ``grid`` describes a real cut of the sheet."""
    f = grid.frame
    if not (0 <= f.x1 < f.x2 <= GRID_SCALE and 0 <= f.y1 < f.y2 <= GRID_SCALE):
        raise SliceRefused("The frame has to lie on the sheet and have some size.")
    if grid.rows > MAX_GRID or grid.cols > MAX_GRID:
        raise SliceRefused(f"A grid has at most {MAX_GRID} rows and {MAX_GRID} columns.")
    for lines, low, high, what in ((grid.row_lines, f.y1, f.y2, "row"), (grid.col_lines, f.x1, f.x2, "column")):
        edges = [low, *lines, high]
        if any(a >= b for a, b in zip(edges, edges[1:])):
            raise SliceRefused(f"The {what} lines have to lie inside the frame, in order, apart from each other.")
    if not grid.cells:
        raise SliceRefused("Mark at least one cell as holding a receipt, or unslice the sheet.")
    if len({tuple(cell) for cell in grid.cells}) != len(grid.cells):
        raise SliceRefused("A cell is listed twice.")
    if any(not (1 <= r <= grid.rows and 1 <= c <= grid.cols) for r, c in grid.cells):
        raise SliceRefused("A filled cell lies outside the grid.")
    if (grid.rows, grid.cols) == (1, 1) and (f.x1, f.y1, f.x2, f.y2) == (0, 0, GRID_SCALE, GRID_SCALE):
        raise SliceRefused("A single cell covering the whole sheet cuts nothing off.")


def filled_cells(grid: SheetGrid) -> list[tuple[int, int]]:
    """The filled cells in reading order: row by row, left to right."""
    return sorted((r, c) for r, c in grid.cells)


def cell_box(grid: SheetGrid, row: int, col: int) -> Box:
    """The cell's rectangle on the sheet (0-1000 scale)."""
    ys = [grid.frame.y1, *grid.row_lines, grid.frame.y2]
    xs = [grid.frame.x1, *grid.col_lines, grid.frame.x2]
    return Box(x1=xs[col - 1], y1=ys[row - 1], x2=xs[col], y2=ys[row])


def crop_filename(sheet_filename: str, row: int, col: int, box: Box) -> str:
    """Where a cell's crop lives, relative to the input folder: named for what it is cut from."""
    sheet = Path(sheet_filename)
    return f"{SLICES_DIR}/{sheet.stem}.r{row}c{col}.{box.x1}-{box.y1}-{box.x2}-{box.y2}{sheet.suffix}"


# =====================================================================================
# A batch's crops
# =====================================================================================
def scan_serials(batch: ScanBatch) -> list[int]:
    """The scanner's own pages (every page that isn't a crop), in order."""
    return sorted(s for s in batch.files if s not in batch.slices)


@dataclass(frozen=True)
class Crop:
    serial: int
    of: SliceOf
    filename: str
    box: Box


def derive_crops(batch: ScanBatch, grids: dict[int, SheetGrid]) -> list[Crop]:
    """The crops ``grids`` make of this batch, numbered on from its last scanned page."""
    scans = scan_serials(batch)
    serial = (scans[-1] if scans else 0) + 1
    crops = []
    for sheet in sorted(grids):
        for row, col in filled_cells(grids[sheet]):
            box = cell_box(grids[sheet], row, col)
            crops.append(Crop(serial, SliceOf(sheet=sheet, row=row, col=col),
                              crop_filename(batch.files[sheet], row, col, box), box))
            serial += 1
    return crops


def current_crops(batch: ScanBatch) -> list[Crop]:
    """The crops as the batch records them."""
    return [Crop(serial, of, batch.files[serial], cell_box(batch.grids[of.sheet], of.row, of.col))
            for serial, of in sorted(batch.slices.items())]


def validate_slices(batch: ScanBatch) -> None:
    """Raise ValueError unless the batch's crops are exactly what its grids make, without gaps."""
    scans = set(scan_serials(batch))
    for sheet, grid in batch.grids.items():
        if sheet not in scans:
            raise ValueError(f"batch {batch.batch_id}: sheet {sheet} is not a scanned page")
        validate_grid(grid)
    expected = derive_crops(batch, batch.grids)
    recorded = {serial: (of, batch.files.get(serial)) for serial, of in batch.slices.items()}
    if recorded != {c.serial: (c.of, c.filename) for c in expected}:
        raise ValueError(f"batch {batch.batch_id}: its crops are not what its grids make")


def with_grids(batch: ScanBatch, grids: dict[int, SheetGrid]) -> ScanBatch:
    """The batch with ``grids``, its crops renumbered to match."""
    crops = derive_crops(batch, grids)
    files = {s: batch.files[s] for s in scan_serials(batch)} | {c.serial: c.filename for c in crops}
    return batch.model_copy(update={"files": files, "grids": dict(grids),
                                    "slices": {c.serial: c.of for c in crops}})


def sheet_of(batch: ScanBatch, serial: int) -> int | None:
    """The sheet a crop was cut from, or None for a scanned page."""
    of = batch.slices.get(serial)
    return of.sheet if of else None


# =====================================================================================
# Planning a change
# =====================================================================================
def sliced_decision() -> ReviewDecision:
    return ReviewDecision(verdict="tossed", toss_reason="sliced", document_type="other", name="", date="", time="")


@dataclass
class SlicePlan:
    batch_id: int
    sheet: int
    grid: SheetGrid | None
    #: the crops after the change, and which of them keep their results
    crops: list[Crop]
    kept: set[int]
    #: (old serial, new serial) for crops that only move
    moved: list[tuple[int, int]]
    #: page keys whose mid-ingest results are dropped (they name another crop now, or nothing)
    dropped_keys: set[str]
    ocr: int
    extractions: int
    decisions: int
    #: trims dropped: the moved crops', and the sheet's own when it is sliced
    trims: int
    #: the sheet's own decision, when slicing replaces one a person made
    replaces_decision: bool
    token: str

    @property
    def new_keys(self) -> list[str]:
        return [batch_serial_key(self.batch_id, c.serial) for c in self.crops if c.of.sheet == self.sheet]


def _batch(output_path: Path, batch_id: int) -> tuple[ScanIndex, ScanBatch]:
    index = load_scan_index(output_path) if (output_path / "batches.json").exists() else None
    batch = next((b for b in index.batches if b.batch_id == batch_id), None) if index else None
    if batch is None or batch.archived:
        raise KeyError(f"no unarchived batch {batch_id}")
    return index, batch


def why_not(batch: ScanBatch, sheet: int, decisions: dict[str, ReviewDecision], groups: list[list[str]]) -> str | None:
    """Why this page can't be sliced, or None if it can."""
    key = batch_serial_key(batch.batch_id, sheet)
    decision = decisions.get(key)
    if sheet in batch.slices:
        return f"{key} is a crop; only a scanned sheet can be sliced."
    if decision is not None and decision.verdict == "tossed" and not decision.sliced:
        return f"{key} was tossed; recover it before slicing it."
    if any(len(group) > 1 and key in group for group in groups):
        return f"{key} is linked into a multi-page document; unlink it before slicing it."
    return None


def plan_slices(output_path: Path, batch_id: int, sheet: int, grid: SheetGrid | None) -> SlicePlan:
    """What cutting ``sheet`` by ``grid`` (None: unslicing it) would do, for the page to confirm."""
    _, batch = _batch(output_path, batch_id)
    key = batch_serial_key(batch_id, sheet)
    if sheet in batch.slices:
        raise SliceRefused(f"{key} is a crop; only a scanned sheet can be sliced.")
    if sheet not in batch.files:
        raise KeyError(f"no page {key}")
    decisions = load_decisions(output_path)
    decision = decisions.get(key)
    if grid is not None:
        validate_grid(grid)
        refusal = why_not(batch, sheet, decisions, load_document_groups(output_path).groups)
        if refusal:
            raise SliceRefused(refusal)
    elif sheet not in batch.grids and not (decision and decision.sliced):
        raise SliceRefused(f"{key} isn't sliced.")

    grids = dict(batch.grids)
    if grid is None:
        grids.pop(sheet, None)
    else:
        grids[sheet] = grid
    old = {c.serial: c for c in current_crops(batch)}
    new = derive_crops(batch, grids)
    kept = {c.serial for c in new if c.serial in old and old[c.serial].of == c.of and old[c.serial].box == c.box}
    touched = (set(old) | {c.serial for c in new}) - kept
    dropped = {batch_serial_key(batch_id, s) for s in touched}
    old_serial_of = {(c.of.sheet, c.of.row, c.of.col): c.serial for c in old.values()}
    moved = sorted((old_serial_of[(c.of.sheet, c.of.row, c.of.col)], c.serial) for c in new
                   if (c.of.sheet, c.of.row, c.of.col) in old_serial_of
                   and old_serial_of[(c.of.sheet, c.of.row, c.of.col)] != c.serial)

    ocr = load_ocr_batch(output_path, batch_id)
    extractions = load_extractions(output_path)
    trims = load_trims(output_path)
    counts = (len(dropped & set(ocr)), len(dropped & set(extractions)), len(dropped & set(decisions)),
              len((dropped | ({key} if grid is not None else set())) & set(trims)))
    replaces = grid is not None and decision is not None and not decision.sliced
    token = hashlib.sha1(json.dumps([
        batch.model_dump(mode="json"), grid.model_dump(mode="json") if grid else None,
        decision.model_dump(mode="json") if decision else None, counts,
    ], sort_keys=True).encode()).hexdigest()
    return SlicePlan(batch_id=batch_id, sheet=sheet, grid=grid, crops=new, kept=kept, moved=moved,
                     dropped_keys=dropped, ocr=counts[0], extractions=counts[1], decisions=counts[2],
                     trims=counts[3], replaces_decision=replaces, token=token)


# =====================================================================================
# Applying it
# =====================================================================================
def cut_crop(sheet_path: Path, box: Box, target: Path) -> None:
    """Cut ``box`` out of the sheet into ``target``, in the sheet's own format.

    Written to a sibling file and swapped in, so a crash never leaves a half-written crop.
    """
    with Image.open(sheet_path) as opened:
        image_format = opened.format
        img = ImageOps.exif_transpose(opened)   # the grid was drawn on the sheet as a browser shows it
        width, height = img.size
        pixels = (round(box.x1 * width / GRID_SCALE), round(box.y1 * height / GRID_SCALE),
                  round(box.x2 * width / GRID_SCALE), round(box.y2 * height / GRID_SCALE))
        crop = img.crop(pixels)
        if image_format == "JPEG" and crop.mode not in ("RGB", "L"):
            crop = crop.convert("RGB")
        crop.load()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.stem}.cutting{target.suffix}")
    try:
        crop.save(tmp, format=image_format, **({"quality": 95} if image_format == "JPEG" else {}))
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def _sweep(input_path: Path, index: ScanIndex) -> None:
    """Delete crop files no batch refers to (left by a change, or by an apply that crashed)."""
    folder = input_path / SLICES_DIR
    if not folder.is_dir():
        return
    used = {fn for b in index.batches for fn in b.files.values() if fn.startswith(f"{SLICES_DIR}/")}
    for path in folder.iterdir():
        if path.is_file() and f"{SLICES_DIR}/{path.name}" not in used:
            path.unlink()


def apply_slices(output_path: Path, input_path: Path, batch_id: int, sheet: int, grid: SheetGrid | None,
                 token: str) -> SlicePlan:
    """Cut the sheet (or put it back), if the plan is still the one the user saw.

    The caller holds the batch (no job may be working on it) and the decisions lock.
    """
    plan = plan_slices(output_path, batch_id, sheet, grid)
    if plan.token != token:
        raise StaleSlicePlan("The batch changed since the preview; review the new one.")
    index, batch = _batch(output_path, batch_id)
    sheet_key = batch_serial_key(batch_id, sheet)

    # 1. the crops: new names only ever hold what they say, so this changes nothing the batch refers to yet
    for crop in plan.crops:
        target = input_path / crop.filename
        if crop.serial not in plan.kept or not target.exists():
            cut_crop(input_path / batch.files[crop.of.sheet], crop.box, target)

    # 2. results that name another crop now (or nothing) go, as does what Parse made of the sheet itself
    ocr = load_ocr_batch(output_path, batch_id)
    if plan.dropped_keys & set(ocr):
        save_ocr_batch(output_path, batch_id, {k: v for k, v in ocr.items() if k not in plan.dropped_keys})
    drop_model_runs(output_path, OCR_RUNS, plan.dropped_keys)
    drop_extractions(output_path, plan.dropped_keys | {sheet_key})
    drop_model_runs(output_path, EXTRACTION_RUNS, plan.dropped_keys | {sheet_key})
    drop_trims(output_path, plan.dropped_keys | {sheet_key})
    decisions = {k: v for k, v in load_decisions(output_path).items() if k not in plan.dropped_keys}
    if grid is None:
        decisions.pop(sheet_key, None)
    else:
        decisions[sheet_key] = sliced_decision()
    save_decisions(output_path, decisions)

    # 3. the batch, in one write; then the crops nothing refers to any more
    changed = with_grids(batch, {**batch.grids, sheet: grid} if grid else
                         {s: g for s, g in batch.grids.items() if s != sheet})
    validate_slices(changed)
    index.batches = [changed if b.batch_id == batch_id else b for b in index.batches]
    save_scan_index(output_path, index)
    _sweep(input_path, index)
    return plan


# =====================================================================================
# Consistency
# =====================================================================================
def mismatches(batch: ScanBatch, decisions: dict[str, ReviewDecision]) -> list[str]:
    """Sheets whose grid and sliced toss disagree (an apply that was interrupted), as messages."""
    problems = []
    for sheet in sorted(batch.grids):
        decision = decisions.get(batch_serial_key(batch.batch_id, sheet))
        if not (decision and decision.sliced):
            problems.append(f"{batch_serial_key(batch.batch_id, sheet)} is sliced but not tossed as sliced")
    for key, decision in decisions.items():
        left, _, right = key.partition(":")
        if decision.sliced and left == str(batch.batch_id) and right.isdigit() and int(right) not in batch.grids:
            problems.append(f"{key} is tossed as sliced but has no grid")
    return problems
