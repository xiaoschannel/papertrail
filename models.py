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


class ScanIndex(BaseModel):
    batches: list[ScanBatch]


def load_scan_index(output_path: Path) -> tuple["ScanIndex", dict[str, int]]:
    index = ScanIndex.model_validate_json((output_path / "batches.json").read_text(encoding="utf-8"))
    filename_to_batch: dict[str, int] = {}
    for batch in index.batches:
        for fn in batch.files.values():
            filename_to_batch[fn] = batch.batch_id
    return index, filename_to_batch
