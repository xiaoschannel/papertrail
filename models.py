from pathlib import Path
from typing import Annotated, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

T = TypeVar("T")

#: A grid cell, [row, col] from 1. A list rather than a tuple: the web app's generated client widens tuples
#: in responses, so a tuple here would give the same model two unrelated TypeScript types.
Cell = Annotated[list[int], Field(min_length=2, max_length=2)]


class DetectedBox(BaseModel):
    ref_type: str
    coords: list[list[int]]
    text: str | None = None


class TokenUse(BaseModel):
    """What one extraction call consumed: the few numbers pricing needs, and the response verbatim.

    ``cached`` is the part of ``prompt`` that was billed at a tenth; ``thinking`` is part of
    ``completion``. ``raw`` is the provider's own payload -- its ``usage`` object as it arrived, with
    the response fields that say which model and machine served the call -- for when the numbers look
    wrong and the summary is the thing in doubt.
    """

    prompt: int = 0
    cached: int = 0
    completion: int = 0
    thinking: int = 0
    raw: dict | None = None


class ModelRun(BaseModel):
    """What one model call on one item took: which model, when it finished, and what it consumed.

    The same record for reading a scan and for extracting from it. ``tokens`` and ``cost`` are absent
    for a model that runs on this machine, which is what OCR is today and need not always be.
    """

    model: str
    at: float                            # epoch seconds, when the call returned
    seconds: float
    tokens: TokenUse | None = None
    cost: float | None = None


#: The thinnest band a trim may keep, as a fraction of the scan's height.
MIN_TRIM_BAND = 0.02


class Trim(BaseModel):
    """The part of a page scan that is the document: the band between two horizontal cuts.

    ``top`` and ``bottom`` are fractions of the scan's height as the file is stored (0 is its top
    edge, 1 its bottom). A coupon or survey printed under a receipt, or a header above it, gives OCR
    more to misread; a trim keeps it from being read or shown, while the scan itself is never changed,
    so a cut can always be moved back out. OCR reads only the band, and every display shows only the
    band, with a shadow on each edge that was cut.

    A page that isn't trimmed has no ``Trim`` at all (``None``), never a band from 0 to 1: see
    ``trim_of``. That way "trimmed the same way" is plain equality.
    """

    model_config = ConfigDict(frozen=True)

    top: float = Field(0.0, ge=0.0, le=1.0)
    bottom: float = Field(1.0, ge=0.0, le=1.0)

    @field_validator("top", "bottom")
    @classmethod
    def _rounded(cls, value: float) -> float:
        return round(value, 4)       # a dragged ruler's float noise shouldn't count as a different cut

    @model_validator(mode="after")
    def _keeps_something(self) -> "Trim":
        # with a little give: the rulers stop exactly MIN_TRIM_BAND apart, which in floating point can come
        # out a hair under it (0.3 - 0.28)
        if self.bottom - self.top < MIN_TRIM_BAND - 1e-9:
            raise ValueError(f"a trim has to keep at least {MIN_TRIM_BAND:.0%} of the page")
        return self

    def rows(self, height: int) -> tuple[int, int]:
        """The band as pixel rows ``[first, end)`` of a scan ``height`` pixels tall; never empty."""
        first = min(max(0, round(self.top * height)), height - 1)
        return first, max(first + 1, min(height, round(self.bottom * height)))

    def upside_down(self) -> "Trim":
        """The same band once the scan is turned 180°."""
        return Trim(top=round(1 - self.bottom, 4), bottom=round(1 - self.top, 4))


def trim_of(top: float, bottom: float) -> Trim | None:
    """A trim from its two cuts, or None when they keep the whole page."""
    band = Trim(top=top, bottom=bottom)
    return None if band.top == 0 and band.bottom == 1 else band


def turned_trim(band: Trim | None, top_points: str) -> Trim | None:
    """A page's trim once its file is turned upright from ``top_points`` (File Index's arrows).

    Upside down, the band flips. A quarter turn would lay it across the page's width, which a trim
    can't be, so the page is left whole.
    """
    if band is None or top_points == "":
        return band
    return band.upside_down() if top_points == "down" else None


def reframe_y(y: int, read: Trim | None, shown: Trim | None) -> int:
    """A height on the 0-1000 scale of the band ``read`` was measured on, placed on the band ``shown``.

    OCR measures its boxes on the image it was given, which is the band the page was trimmed to when it
    was read. The page is shown trimmed as it is now; the two differ once the cut moves after the read.
    """
    if read == shown:
        return y
    read_top, read_bottom = (read.top, read.bottom) if read else (0.0, 1.0)
    shown_top, shown_bottom = (shown.top, shown.bottom) if shown else (0.0, 1.0)
    on_scan = read_top + y / 1000 * (read_bottom - read_top)
    return round((on_scan - shown_top) / (shown_bottom - shown_top) * 1000)


class OcrResult(BaseModel):
    markdown: str
    boxes: list[DetectedBox] | None = None
    succeeded: bool = True
    #: the band of the page this was read from (None: the whole page); its boxes are measured on it
    trim: Trim | None = None


def ocr_page_section(page_num: int, result: OcrResult) -> str:
    """One page as the extractor reads it: the OCR text, then its grounding boxes tagged ``[P<page>-BOX-<i>]``."""
    section = f"--- Page {page_num} ---\n{result.markdown}"
    if result.boxes:
        box_lines = [f"[P{page_num}-BOX-{idx}] {box.text}" for idx, box in enumerate(result.boxes)]
        section += f"\n--- Page {page_num} Grounding Boxes ---\n" + "\n".join(box_lines)
    return section


class ReceiptItem(BaseModel):
    name: str
    quantity: float | None = None
    unit_price: float | None = None
    total_price: float | None = None


class ReceiptResult(BaseModel):
    document_type: Literal["receipt"]
    language: str
    date: str
    time: str
    name: str
    phone: str = ""
    currency: str
    address: str
    items: list[ReceiptItem] = []
    cost: float
    field_sources: dict[str, list[str]] = {}


class OtherResult(BaseModel):
    document_type: Literal["other"]
    language: str
    date: str
    time: str
    title: str
    field_sources: dict[str, list[str]] = {}


class CorruptedResult(BaseModel):
    document_type: Literal["corrupted"]


DocumentExtraction = Annotated[
    Union[ReceiptResult, OtherResult, CorruptedResult],
    Field(discriminator="document_type"),
]

DocumentExtractionAdapter = TypeAdapter(DocumentExtraction)


class FieldSourceEntry(BaseModel):
    field: str
    boxes: list[str]


class ExtractionFlat(BaseModel):
    document_type: Literal["receipt", "other", "corrupted"]
    language: str = ""
    date: str = ""
    time: str = ""
    name: str = ""
    title: str = ""
    phone: str = ""
    currency: str = ""
    address: str = ""
    items: list[ReceiptItem] = []
    cost: float = 0.0
    field_sources: list[FieldSourceEntry] = []

    def to_extraction(self) -> DocumentExtraction:
        sources = {fs.field: fs.boxes for fs in self.field_sources}
        if self.document_type == "corrupted":
            return CorruptedResult(document_type="corrupted")
        if self.document_type == "other":
            return OtherResult(
                document_type="other",
                language=self.language,
                date=self.date,
                time=self.time,
                title=self.title,
                field_sources=sources,
            )
        return ReceiptResult(
            document_type="receipt",
            language=self.language,
            date=self.date,
            time=self.time,
            name=self.name,
            phone=self.phone,
            currency=self.currency,
            address=self.address,
            items=self.items,
            cost=self.cost,
            field_sources=sources,
        )


Verdict = Literal["accepted", "marked", "tossed"]

VERDICT_COLORS: dict[Verdict, str] = {
    "accepted": "#28a745",
    "marked": "#ffc107",
    "tossed": "#6c757d",
}

VERDICT_LABELS: dict[Verdict, str] = {
    "accepted": "Accepted",
    "marked": "Marked",
    "tossed": "Tossed",
}


#: Why a document was tossed, when it wasn't a person's call. "sliced": the page is a sheet of small
#: receipts that was cut into crops (``ScanBatch.grids``); only slicing sets or clears this toss.
TossReason = Literal["sliced"]


class ReviewDecision(BaseModel):
    verdict: Verdict
    document_type: str
    name: str
    date: str
    time: str
    cost: float = 0.0
    currency: str = ""
    comment: str = ""
    toss_reason: TossReason | None = None

    @property
    def sliced(self) -> bool:
        return self.verdict == "tossed" and self.toss_reason == "sliced"


class SmartMatchHistoryRow(BaseModel):
    extracted: str
    extracted_phone: str
    confirmed: str


class SmartMatchCandidate(BaseModel):
    confirmed_name: str
    name_score: float
    phone_score: float
    combined_score: float
    quick_apply: bool


class Sidecar(BaseModel):
    original_filename: str
    batch_id: int | None = None
    serial: int | None = None
    review: ReviewDecision
    document_key: str | None = None
    #: the page's place in its document, from 1: the order the pages were grouped in, not scan order
    page: int = 1
    ocr: OcrResult | None = None
    extraction: DocumentExtraction | None = None
    #: what the two model calls behind this document took; absent for documents filed before this was kept
    ocr_run: ModelRun | None = None
    extraction_run: ModelRun | None = None
    #: a crop: the sheet it was cut from ("batch:serial"), its grid cell and the cell's box on the sheet
    slice_of: str | None = None
    slice_cell: Cell | None = None
    slice_box: "Box | None" = None
    #: the part of the scan that is the document (None: all of it)
    trim: Trim | None = None


#: Grid coordinates are on a 0-1000 scale of the sheet image, like OCR boxes, so they never need its size.
GRID_SCALE = 1000
#: The most rows or columns a sheet's grid may have.
MAX_GRID = 12


class Box(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class SheetGrid(BaseModel):
    """How a sheet of small receipts taped together is cut: an m x n grid over ``frame``.

    ``row_lines`` and ``col_lines`` are the m-1 and n-1 dividers inside the frame; ``cells`` are the
    [row, col] (from 1) that hold a receipt, since a grid can run out of receipts before it runs out of
    cells. The sheet is already upright, so every crop inherits its rotation.
    """

    frame: Box
    row_lines: list[int] = []
    col_lines: list[int] = []
    cells: list[Cell]

    @property
    def rows(self) -> int:
        return len(self.row_lines) + 1

    @property
    def cols(self) -> int:
        return len(self.col_lines) + 1


class SliceOf(BaseModel):
    """Where a crop was cut from: its sheet's serial and the grid cell."""

    sheet: int
    row: int
    col: int


class ScanBatch(BaseModel):
    """A scanner run. Serials run without gaps: the scanner's own pages first, then the crops cut from
    sliced sheets (``slices``), in sheet order and, within a sheet, cell by cell in reading order."""

    batch_id: int
    start_datetime: str
    end_datetime: str
    files: dict[int, str]
    archived: bool = False
    #: sheet serial -> how it is cut
    grids: dict[int, SheetGrid] = Field(default_factory=dict, exclude_if=lambda v: not v)
    #: crop serial -> where it was cut from
    slices: dict[int, SliceOf] = Field(default_factory=dict, exclude_if=lambda v: not v)


class ScanIndex(BaseModel):
    batches: list[ScanBatch]


def load_scan_index(output_path: Path) -> "ScanIndex":
    return ScanIndex.model_validate_json((output_path / "batches.json").read_text(encoding="utf-8"))


def iter_indexed_files(index: "ScanIndex", include_archived: bool = True) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for batch in index.batches:
        if not include_archived and batch.archived:
            continue
        for serial, fn in batch.files.items():
            out.append((batch.batch_id, serial, fn))
    return out


def filename_to_batch_serial(index: "ScanIndex") -> dict[str, tuple[int, int]]:
    return {fn: (batch_id, serial) for batch_id, serial, fn in iter_indexed_files(index)}


class FileKey:
    __slots__ = ("batch_id", "serial")

    def __init__(self, batch_id: int, serial: int):
        self.batch_id = batch_id
        self.serial = serial

    @classmethod
    def parse(cls, s: str) -> "FileKey | None":
        parts = s.split(":")
        if len(parts) != 2:
            return None
        try:
            return cls(int(parts[0]), int(parts[1]))
        except ValueError:
            return None

    def __str__(self) -> str:
        return f"{self.batch_id}:{self.serial}"

    def __hash__(self) -> int:
        return hash((self.batch_id, self.serial))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FileKey):
            return self.batch_id == other.batch_id and self.serial == other.serial
        return False


def batch_serial_key(batch_id: int, serial: int) -> str:
    return str(FileKey(batch_id, serial))


def parse_batch_serial_key(key: str) -> tuple[int, int] | None:
    file_key = FileKey.parse(key)
    return (file_key.batch_id, file_key.serial) if file_key else None


class DocumentKey:
    __slots__ = ("batch_id", "first_serial", "last_serial")

    def __init__(self, batch_id: int, first_serial: int, last_serial: int):
        self.batch_id = batch_id
        self.first_serial = first_serial
        self.last_serial = last_serial

    @classmethod
    def parse(cls, s: str) -> "DocumentKey | None":
        if ":" not in s:
            return None
        left, right = s.split(":", 1)
        try:
            batch_id = int(left)
        except ValueError:
            return None
        if "-" in right:
            parts = right.split("-", 1)
            try:
                first_serial = int(parts[0])
                last_serial = int(parts[1])
                return cls(batch_id, first_serial, last_serial)
            except (ValueError, IndexError):
                return None
        try:
            serial = int(right)
            return cls(batch_id, serial, serial)
        except ValueError:
            return None

    @classmethod
    def from_group(cls, keys: list[str]) -> "DocumentKey":
        if not keys:
            raise ValueError("Empty group")
        parsed = [FileKey.parse(k) for k in keys]
        if not all(p for p in parsed):
            raise ValueError("Invalid key in group")
        if not all(p.batch_id == parsed[0].batch_id for p in parsed):
            raise ValueError("Document must be contiguous within same batch")
        batch_id = parsed[0].batch_id
        serials = [p.serial for p in parsed]
        return cls(batch_id, min(serials), max(serials))

    @property
    def is_multi_page(self) -> bool:
        return self.first_serial != self.last_serial

    def __str__(self) -> str:
        if self.first_serial == self.last_serial:
            return f"{self.batch_id}:{self.first_serial}"
        return f"{self.batch_id}:{self.first_serial}-{self.last_serial}"

    def __hash__(self) -> int:
        return hash((self.batch_id, self.first_serial, self.last_serial))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DocumentKey):
            return (
                self.batch_id == other.batch_id
                and self.first_serial == other.first_serial
                and self.last_serial == other.last_serial
            )
        return False


class DocumentGroups(BaseModel):
    groups: list[list[str]] = []


class DocumentIndex:
    def __init__(self, doc_to_keys: dict[DocumentKey, list[str]]):
        self._doc_to_keys = doc_to_keys
        self._key_to_doc: dict[str, DocumentKey] = {}
        for doc_key, keys in doc_to_keys.items():
            for k in keys:
                self._key_to_doc[k] = doc_key

    @classmethod
    def from_raw_groups(
        cls,
        raw_groups: list[list[str]],
        indexed_keys: set[str],
        ocr_keys: set[str] | None = None,
    ) -> "DocumentIndex":
        raw = raw_groups
        if not raw:
            keys = indexed_keys & (ocr_keys or indexed_keys) if ocr_keys is not None else indexed_keys
            return cls({DocumentKey.parse(k) or DocumentKey.from_group([k]): [k] for k in keys})
        valid_groups = [g for g in raw if len(g) > 1 and all(k in indexed_keys for k in g)]
        doc_to_keys: dict[DocumentKey, list[str]] = {}
        for g in valid_groups:
            if g:
                doc_key = DocumentKey.from_group(g)
                doc_to_keys[doc_key] = g
        keys_in_groups = {k for keys in doc_to_keys.values() for k in keys}
        for k in indexed_keys:
            if k not in keys_in_groups:
                if ocr_keys is not None and k not in ocr_keys:
                    continue
                doc_key = DocumentKey.parse(k) or DocumentKey.from_group([k])
                doc_to_keys[doc_key] = [k]
        return cls(doc_to_keys)

    def key_to_doc_key(self, file_key: str) -> DocumentKey:
        return self._key_to_doc.get(file_key, DocumentKey.parse(file_key) or DocumentKey.from_group([file_key]))

    def keys_for_doc(self, doc_key: DocumentKey) -> list[str]:
        return self._doc_to_keys.get(doc_key, [str(doc_key)])

    def doc_keys(self) -> list[DocumentKey]:
        return list(self._doc_to_keys)

    def doc_keys_with_ocr(self, ocr_by_key: dict[str, str]) -> list[DocumentKey]:
        return [doc_key for doc_key, keys in self._doc_to_keys.items() if all(k in ocr_by_key for k in keys)]

    def concat_ocr(self, doc_key: DocumentKey, ocr_by_key: dict[str, str]) -> str:
        parts = []
        for i, k in enumerate(self.keys_for_doc(doc_key)):
            if k in ocr_by_key:
                parts.append(f"--- Page {i + 1} ---\n{ocr_by_key[k]}")
        return "\n\n".join(parts)

    def concat_ocr_with_boxes(
        self,
        doc_key: DocumentKey,
        ocr_results: "dict[str, OcrResult]",
    ) -> tuple[str, bool]:
        parts = []
        has_boxes = False
        for i, k in enumerate(self.keys_for_doc(doc_key)):
            r = ocr_results.get(k)
            if not r or not r.succeeded:
                continue
            has_boxes = has_boxes or bool(r.boxes)
            parts.append(ocr_page_section(i + 1, r))
        return "\n\n".join(parts), has_boxes

    def expand_decisions(self, decisions: dict[DocumentKey, T]) -> dict[str, T]:
        result: dict[str, T] = {}
        for doc_key, val in decisions.items():
            for k in self.keys_for_doc(doc_key):
                result[k] = val
        return result
