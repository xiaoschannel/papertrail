from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class DetectedBox(BaseModel):
    ref_type: str
    coords: list[list[int]]
    text: str | None = None


class OcrResult(BaseModel):
    filename: str
    raw: str
    boxes: list[DetectedBox] | None = None
    markdown: str
    succeeded: bool = True


class OcrBatch(BaseModel):
    results: list[OcrResult]


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


class OtherResult(BaseModel):
    document_type: Literal["other"]
    language: str
    date: str
    time: str
    title: str


class CorruptedResult(BaseModel):
    document_type: Literal["corrupted"]


DocumentExtraction = Annotated[
    Union[ReceiptResult, OtherResult, CorruptedResult],
    Field(discriminator="document_type"),
]

DocumentExtractionAdapter = TypeAdapter(DocumentExtraction)


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

    def to_extraction(self) -> DocumentExtraction:
        if self.document_type == "corrupted":
            return CorruptedResult(document_type="corrupted")
        if self.document_type == "other":
            return OtherResult(
                document_type="other",
                language=self.language,
                date=self.date,
                time=self.time,
                title=self.title,
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
    return {fn: (bid, ser) for bid, ser, fn in iter_indexed_files(index)}


def batch_serial_key(batch_id: int, serial: int) -> str:
    return f"{batch_id}:{serial}"


def parse_batch_serial_key(key: str) -> tuple[int, int] | None:
    parts = key.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None
