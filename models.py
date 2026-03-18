from pathlib import Path
from typing import Annotated, Literal, TypeVar, Union

from pydantic import BaseModel, Field, TypeAdapter

T = TypeVar("T")


class DetectedBox(BaseModel):
    ref_type: str
    coords: list[list[int]]
    text: str | None = None


class OcrResult(BaseModel):
    markdown: str
    boxes: list[DetectedBox] | None = None
    succeeded: bool = True


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
    currency: str
    location: str
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
    currency: str = ""
    location: str = ""
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
            currency=self.currency,
            location=self.location,
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


class ReviewDecision(BaseModel):
    verdict: Verdict
    document_type: str
    name: str
    date: str
    time: str
    cost: float = 0.0
    currency: str = ""


class Sidecar(BaseModel):
    original_filename: str
    batch_id: int | None = None
    serial: int | None = None
    review: ReviewDecision
    document_key: str | None = None
    ocr: OcrResult | None = None
    extraction: DocumentExtraction | None = None


class ScanBatch(BaseModel):
    batch_id: int
    start_datetime: str
    end_datetime: str
    files: dict[int, str]
    archived: bool = False


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
            page_num = i + 1
            section = f"--- Page {page_num} ---\n{r.markdown}"
            if r.boxes:
                has_boxes = True
                box_lines = [f"[P{page_num}-BOX-{idx}] {box.text}" for idx, box in enumerate(r.boxes)]
                section += f"\n--- Page {page_num} Grounding Boxes ---\n" + "\n".join(box_lines)
            parts.append(section)
        return "\n\n".join(parts), has_boxes

    def expand_decisions(self, decisions: dict[DocumentKey, T]) -> dict[str, T]:
        result: dict[str, T] = {}
        for doc_key, val in decisions.items():
            for k in self.keys_for_doc(doc_key):
                result[k] = val
        return result
