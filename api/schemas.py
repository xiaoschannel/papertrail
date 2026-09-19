"""Request/response models for the visualize, analytics and review endpoints (config uses
``settings.AppConfig``).

Endpoints declare these as ``response_model``, so responses are validated at runtime. FastAPI turns
them into the OpenAPI schema; ``tools/export_openapi.py`` snapshots it to ``frontend/openapi.json``
and ``openapi-typescript`` generates the frontend's types (``frontend/src/api/schema.d.ts``) from that.

Shapes mirror what the pure core already produces (``viz_records``, ``analytics``), serialized by
``api.serialization``: timestamps are ISO strings, missing values are ``null``. Every field is
required and ``extra="forbid"``, so a column added to or dropped from a frame fails response
validation in the API tests instead of silently drifting from the generated types.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- archived documents ------------------------------------------------------
class LineItem(_Model):
    name: str
    quantity: float | None
    unit_price: float | None
    total_price: float | None


class VizRecord(_Model):
    """One archived document (multi-page documents collapsed to their first page)."""

    filename: str
    path: str
    paths: list[str]
    document_type: str
    name: str
    date: str
    time: str
    cost: float
    currency: str
    comment: str
    address: str
    language: str
    items: list[LineItem]
    ocr_markdown: str
    parsed_date: str | None
    year: float | None
    month: float | None
    brand_id: str | None
    brand_label: str | None
    brand_location: str
    merchant_group: str


class VizItem(_Model):
    """One line item across all archived receipts."""

    filename: str
    merchant: str
    merchant_group: str
    receipt_date: str
    parsed_date: str | None
    item_name: str
    quantity: float | None
    unit_price: float | None
    total_price: float | None
    currency: str


class GalleryReceipt(_Model):
    """Slim receipt for galleries: no OCR text or line items."""

    filename: str
    path: str
    date: str
    time: str
    cost: float
    currency: str
    name: str
    brand_location: str
    document_type: str


# --- analytics -----------------------------------------------------------------
class BrandTotals(_Model):
    merchant_group: str
    total_spend: float
    visit_count: int
    brand_id: str | None
    avg_per_visit: int


class NameTotals(_Model):
    name: str
    total_spend: float
    visit_count: int
    brand_id: str | None
    avg_per_visit: int


class MonthlySpend(_Model):
    period: str
    spend: float
    month_ts: str


class MonthlyVolume(_Model):
    period: str
    count: int
    month_ts: str


class MerchantMetrics(_Model):
    total_spend: float
    visit_count: int
    avg_ticket: float
    currency: str
    first_visit: str | None
    last_visit: str | None
    avg_gap: float | None


class CadencePoint(_Model):
    visit_date: str
    days_since_last: float


class ItemBreakdownRow(_Model):
    item_name: str
    times_purchased: int
    total_spent: float
    avg_unit_price: float | None


class LocationCount(_Model):
    """One branch of a brand: the part of the name after the brand prefix, and how many receipts."""

    location: str
    count: int


class MerchantProfile(_Model):
    metrics: MerchantMetrics
    trend: list[MonthlySpend]
    cadence: list[CadencePoint]
    items: list[ItemBreakdownRow]
    locations: list[LocationCount]
    receipts: list[GalleryReceipt]


DocumentType = Literal["receipt", "other", "corrupted"]


class ReceiptEditIn(_Model):
    """New values for an archived document (``file`` is its VizRecord filename/identity)."""

    file: str
    document_type: DocumentType
    name: str
    date: str
    time: str
    cost: float | None
    currency: str
    address: str = ""
    language: str = ""
    comment: str = ""


class DateRange(_Model):
    min: str | None
    max: str | None


class Health(_Model):
    status: str


# --- review (ingest) ---------------------------------------------------------------
VerdictName = Literal["accepted", "marked", "tossed"]


class VerdictCount(_Model):
    verdict: VerdictName
    label: str
    color: str
    count: int


class ReviewSummary(_Model):
    total: int
    pending: int
    verdicts: list[VerdictCount]


class QueueItem(_Model):
    key: str
    document_type: DocumentType
    label: str


class ReviewQueue(_Model):
    """``blocker`` explains why there is nothing to review yet (no index, no extractions)."""

    blocker: str | None
    summary: ReviewSummary
    items: list[QueueItem]


class FormDefaultsOut(_Model):
    document_type: DocumentType
    name: str
    date: str
    time: str
    cost: float
    currency: str
    phone: str


class SmartMatch(_Model):
    name: str
    name_score: float
    phone_score: float
    quick_apply: bool
    label: str


class BoxRect(_Model):
    """Corner-normalized rectangle on the OCR's 0-1000 scale, relative to the page image."""

    x1: int
    y1: int
    x2: int
    y2: int


class FieldBoxOut(_Model):
    index: int
    fields: list[str]
    rects: list[BoxRect]
    text: str | None


class ReviewPage(_Model):
    file_key: str
    filename: str | None
    image_available: bool
    boxes: list[FieldBoxOut]


class DecisionOut(_Model):
    verdict: VerdictName
    document_type: str
    name: str
    date: str
    time: str
    cost: float
    currency: str
    comment: str


class ReviewDocument(_Model):
    key: str
    defaults: FormDefaultsOut
    initial_name: str
    ocr_text: str
    pages: list[ReviewPage]
    smart_matches: list[SmartMatch]
    decision: DecisionOut | None


class DraftIn(_Model):
    document_type: DocumentType
    name: str
    date: str
    time: str
    cost: float | None
    currency: str


class HintsRequest(_Model):
    key: str
    draft: DraftIn


class HintOut(_Model):
    message: str
    color: str


class HintsResponse(_Model):
    """``accept_error`` is why Accept would be refused for these values (None if it wouldn't be)."""

    hints: list[HintOut]
    name_status: Literal["placeholder", "approved", "unseen"]
    accept_error: str | None


class DecisionIn(_Model):
    key: str
    verdict: VerdictName
    draft: DraftIn
    comment: str = ""


# --- jobs --------------------------------------------------------------------------
class JobError(_Model):
    item: str
    error: str


class JobOut(_Model):
    id: str
    kind: str
    title: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    total: int
    done: int
    failed: int
    message: str
    errors: list[JobError]
    elapsed_seconds: float
    seconds_per_item: float | None
    eta_seconds: int | None
    cancel_requested: bool
    version: int


# --- ingest: file index -------------------------------------------------------------
class BatchFile(_Model):
    serial: int
    filename: str


class BatchOut(_Model):
    batch_id: int
    start_datetime: str
    end_datetime: str
    file_count: int


class ProposedBatch(BatchOut):
    files: list[BatchFile]


class IndexStatus(_Model):
    """``blocker`` explains why indexing can't be shown (paths not configured, input folder missing)."""

    blocker: str | None
    schemes: list[str]
    scheme: str
    image_count: int
    indexed_count: int
    unindexed_count: int
    existing_batches: int
    proposal: list[ProposedBatch]
    skipped: list[str]
    warnings: list[str]
    error: str | None
    offending: list[str]
    token: str


class ConfirmIndexIn(_Model):
    scheme: str
    token: str


class GroupingPageOut(_Model):
    key: str
    serial: int
    filename: str
    tossed: bool
    image_available: bool
    image_version: int


class GroupingOut(_Model):
    blocker: str | None
    batches: list[BatchOut]
    batch_id: int | None
    pages: list[GroupingPageOut]
    display_keys: list[str]
    active_links: list[bool]
    saved_groups: list[list[str]]


class SaveGroupingIn(_Model):
    batch_id: int
    groups: list[list[str]]


class SaveGroupingOut(_Model):
    changed: bool


class PageIn(_Model):
    key: str


class RotateIn(_Model):
    key: str
    top_points: Literal["left", "right", "down"]


# --- ingest: OCR / Parse / Archive -----------------------------------------------------
class OcrStatus(_Model):
    blocker: str | None
    providers: list[str]
    provider: str
    grounding: bool
    batches: list[BatchOut]
    total: int
    processed: int
    failed: int
    missing_images: int
    to_process: int


class StartOcrIn(_Model):
    provider: str
    batch_id: int | None = None
    reprocess: bool = False
    limit: int = 0


class ParseStatus(_Model):
    blocker: str | None
    extractors: list[str]
    extractor: str
    custom_instruction: str
    total: int
    processed: int
    tossed: int
    to_process: int


class StartParseIn(_Model):
    extractor: str
    reprocess: bool = False
    limit: int = 0
    custom_instruction: str = ""


class ArchiveMoveOut(_Model):
    key: str
    filename: str
    destination: str


class ArchiveStatus(_Model):
    blocker: str | None
    unarchived_batches: int
    complete_batches: int
    documents: int
    multipage: int
    files: int
    accepted: int
    marked: int
    tossed: int
    moves: list[ArchiveMoveOut]


# --- config options ---------------------------------------------------------------------
class NormalizeEngineOut(_Model):
    id: str
    label: str


class ConfigOptions(_Model):
    """What the Config page's dropdowns can offer (models come from the same registry the jobs use)."""

    ocr_models: list[str]
    extractors: list[str]
    normalize_engines: list[NormalizeEngineOut]
    indexing_schemes: list[str]
    dashboard_rank_by: list[str]
    embedding_threshold_step: float


class PathCheck(_Model):
    """Whether a folder typed into the Config page exists (checked on the server, not the browser)."""

    path: str
    exists: bool
    is_dir: bool
