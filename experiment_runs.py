"""The Experiment bench: one image through OCR and Parse, outside the archive.

For trying a model on a scan before it goes anywhere near the pipeline, seeing why a scan misreads, or
tuning the extraction prompt. Each upload gets a run folder in a scratch directory — never the archive —
holding the image, the treated copy OCR last read, and the latest OCR and Parse results, so the page
can be left and come back to. Only the newest ``KEEP`` runs are kept.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from data import atomic_write_text
from models import DetectedBox, DocumentExtraction, OcrResult, ocr_page_section

#: Where runs live. The sandbox points this at its own folder.
ROOT = Path(tempfile.gettempdir()) / "papertrail-experiment"
KEEP = 20
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_OCR, _PARSE, _SEEN = "ocr.json", "parse.json", "seen.png"


class OcrRecord(BaseModel):
    """The last OCR run: which model read what, what it returned, and how long it took."""

    model: str
    read_at: float                       # when it finished (epoch seconds)
    with_boxes: bool
    treatment: dict                      # scan_enhance.Enhancement, as it was for this run
    markdown: str
    structured_raw: str | None = None    # the grounding pass, verbatim
    boxes: list[DetectedBox] = []
    seconds: float
    structured_seconds: float | None = None


class ParseRecord(BaseModel):
    """The last Parse run."""

    extractor: str
    custom_instruction: str
    extraction: DocumentExtraction
    seconds: float


@dataclass(frozen=True)
class Run:
    id: str
    folder: Path
    image: Path                          # the upload, under its own name

    @property
    def ocr_input(self) -> Path:
        """Where a run writes the treated image for OCR (named after the upload, as the Workshop does)."""
        return self.folder / f"{self.image.stem}.enhanced.png"

    @property
    def seen(self) -> Path:
        """The treated image the last OCR run read: its boxes are measured on this."""
        return self.folder / _SEEN


def _safe_name(filename: str, suffix: str) -> str:
    stem = Path(filename.replace("\\", "/")).stem
    stem = re.sub(r'[<>:"/|?*\x00-\x1f]', "_", stem).strip(" .")
    return f"{stem or 'image'}{suffix}"


def create(filename: str, data: bytes, root: Path | None = None) -> Run:
    """Keep an uploaded image as a new run. ValueError if it isn't an image we can read."""
    from PIL import Image, UnidentifiedImageError

    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"Upload a {', '.join(s.lstrip('.') for s in IMAGE_SUFFIXES)} image.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            too_big = Image.MAX_IMAGE_PIXELS and image.width * image.height > Image.MAX_IMAGE_PIXELS
            image.verify()
    except Image.DecompressionBombError as exc:
        raise ValueError("That image is too large to open.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("That file isn't an image that can be read.") from exc
    if too_big:
        raise ValueError("That image is too large to open.")

    root = root or ROOT
    # 16 hex digits of creation time, then 16 random: still 32 hex, and ids sort by age.
    run_id = f"{time.time_ns():016x}{uuid.uuid4().hex[:16]}"
    folder = root / run_id
    folder.mkdir(parents=True)
    image_path = folder / _safe_name(filename, suffix)
    image_path.write_bytes(data)
    _prune(root, keep=folder)
    return Run(run_id, folder, image_path)


def find(run_id: str, root: Path | None = None) -> Run | None:
    if not _RUN_ID.fullmatch(run_id):
        return None
    folder = (root or ROOT) / run_id
    if not folder.is_dir():
        return None
    image = next((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES
                  and p.name != _SEEN and not p.name.endswith(".enhanced.png")), None)
    return Run(run_id, folder, image) if image else None


def latest(root: Path | None = None) -> Run | None:
    """The run used last, so the page opens where it was left."""
    for folder in _folders_newest_first(root or ROOT):
        run = find(folder.name, root)
        if run:
            return run
    return None


def _folders_newest_first(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    folders = [p for p in root.iterdir() if p.is_dir() and _RUN_ID.fullmatch(p.name)]
    return sorted(folders, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def _prune(root: Path, keep: Path) -> None:
    """Clear out all but the ``KEEP`` runs used last, never ``keep`` (the run just made)."""
    others = [folder for folder in _folders_newest_first(root) if folder != keep]
    for folder in others[KEEP - 1:]:
        shutil.rmtree(folder, ignore_errors=True)


# --- results -------------------------------------------------------------------------------------------
def save_ocr(run: Run, record: OcrRecord, seen: Path) -> None:
    """Keep a finished OCR run with the image it read. The old extraction came from the old text, so it goes."""
    seen.replace(run.seen)
    atomic_write_text(run.folder / _OCR, record.model_dump_json(indent=2))
    (run.folder / _PARSE).unlink(missing_ok=True)


def load_ocr(run: Run) -> OcrRecord | None:
    path = run.folder / _OCR
    return OcrRecord.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def save_parse(run: Run, record: ParseRecord) -> None:
    atomic_write_text(run.folder / _PARSE, record.model_dump_json(indent=2))


def load_parse(run: Run) -> ParseRecord | None:
    path = run.folder / _PARSE
    return ParseRecord.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def extractor_input(record: OcrRecord) -> tuple[str, bool]:
    """What Parse hands the extractor for this reading — the pipeline's own page format, boxes and all."""
    result = OcrResult(markdown=record.markdown, boxes=record.boxes or None)
    return ocr_page_section(1, result), bool(record.boxes)
