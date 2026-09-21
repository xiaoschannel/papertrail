"""Request/response models for every endpoint (config uses ``settings.AppConfig``).

Endpoints declare these as ``response_model``, so responses are validated at runtime. FastAPI turns
them into the OpenAPI schema, and the frontend's TypeScript types are generated from that before every
dev, build and typecheck (``npm --prefix frontend run gen:api``), so the two can't drift apart.

Shapes mirror what the pure core already produces (``viz_records``, ``analytics``), serialized by
``api.serialization``: timestamps are ISO strings, missing values are ``null``. Every field is
required and ``extra="forbid"``, so a column added to or dropped from a frame fails response
validation in the API tests instead of silently drifting from the generated types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from models import DocumentExtraction, ModelRun, SheetGrid, TokenUse

if TYPE_CHECKING:
    from review_logic import FieldBox


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- archived documents ------------------------------------------------------
class LineItem(_Model):
    name: str
    quantity: float | None
    unit_price: float | None
    total_price: float | None


class DocumentListItem(_Model):
    """One archived document, as a picker lists it."""

    filename: str
    name: str
    date: str
    time: str
    document_type: str


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


class DocumentRunsOut(_Model):
    """What the model calls behind one archived document took. Absent for a document filed before
    Papertrail kept them."""

    ocr: ModelRun | None
    extraction: ModelRun | None


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

    @classmethod
    def all_of(cls, boxes: list[FieldBox]) -> list[FieldBoxOut]:
        """review_logic's boxes, as every page that draws them sends them."""
        return [cls(index=b.index, fields=list(b.fields), text=b.text,
                    rects=[BoxRect(x1=r[0], y1=r[1], x2=r[2], y2=r[3]) for r in b.rects]) for b in boxes]


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
    toss_reason: Literal["sliced"] | None = None


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
    #: dollars spent so far and the tokens behind them, for a run calling a model that bills
    spent: float
    calls: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    thinking_tokens: int
    cancel_requested: bool
    cancellable: bool
    version: int
    #: what the job holds while it runs - which controls it locks on the other pages
    batches: list[int]
    gpu: bool
    everything: bool


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
    crop_of: str | None = None          # a crop: the sheet it was cut from
    cell: list[int] | None = None        # ... and its [row, col]
    sliced: bool = False                 # a sheet cut into crops: tossed until it is unsliced


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


# --- ingest: slicing sheets of small receipts ------------------------------------------
class SliceCropOut(_Model):
    key: str
    serial: int
    filename: str
    row: int
    col: int
    image_available: bool
    image_version: int


class SlicingSheetOut(_Model):
    key: str
    serial: int
    filename: str
    image_available: bool
    image_version: int
    grid: SheetGrid | None
    crops: list[SliceCropOut]
    sliced: bool                 # tossed as sliced (a grid without it, or it without a grid, is a problem)
    refusal: str | None          # why it can't be sliced now


class SlicingOut(_Model):
    blocker: str | None
    batches: list[BatchOut]
    batch_id: int | None
    sheets: list[SlicingSheetOut]
    problems: list[str]          # sheets an interrupted slice left inconsistent; saving or unslicing repairs


class SlicePlanIn(_Model):
    key: str
    grid: SheetGrid | None       # None: unslice


class ApplySliceIn(SlicePlanIn):
    token: str


class SliceMoveOut(_Model):
    old_key: str
    new_key: str


class SlicePlanOut(_Model):
    key: str
    new_keys: list[str]          # the sheet's crops after the change
    moved: list[SliceMoveOut]    # crops of other sheets (or this one) that get another serial
    dropped_keys: list[str]      # pages whose OCR, extraction and decision are deleted
    ocr: int
    extractions: int
    decisions: int
    replaces_decision: bool      # the sheet had a review decision, which the sliced toss replaces
    token: str


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
    #: pages that need reading but are in a batch another job holds
    waiting: int


class StartOcrIn(_Model):
    provider: str
    batch_id: int | None = None
    reprocess: bool = False
    limit: int = 0


class ParseStatus(_Model):
    blocker: str | None
    extractors: list[str]
    #: the extractors that run on this machine's GPU, and so can't run beside OCR
    local_extractors: list[str]
    extractor: str
    custom_instruction: str
    total: int
    processed: int
    tossed: int
    to_process: int
    #: documents that need parsing but are in a batch another job holds
    waiting: int


class StartParseIn(_Model):
    extractor: str
    reprocess: bool = False
    limit: int = 0
    custom_instruction: str = ""


class ArchiveMoveOut(_Model):
    key: str
    filename: str
    destination: str
    note: str = ""        # "sliced sheet", or "crop of 1:2"


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


# --- curate: marked workshop -------------------------------------------------------------
class MarkedDocumentOut(_Model):
    """One document waiting in ``marked/`` — every page of it, not one entry per page."""

    key: str
    pages: list[str]
    name: str
    comment: str
    batch_id: int | None


class RereadOut(_Model):
    """A reread of the open document waiting for the decision; the document shows what it read."""

    top_points: Literal["", "left", "right", "down"]   # how the pages were turned for it
    ocr_model: str
    extractor: str


class WorkshopOut(_Model):
    documents: list[MarkedDocumentOut]
    document: ReviewDocument | None
    reread: RereadOut | None
    ocr_models: list[str]
    extractors: list[str]
    ocr_model: str
    extractor: str


class ContextScanOut(_Model):
    """A document beside the one being worked on, and where its scan can be seen."""

    filename: str
    verdict: Literal["accepted", "marked", "tossed", ""]   # "" while it is still being ingested
    name: str
    date: str
    time: str
    cost: float
    currency: str
    image: str | None          # a media URL
    receipt: str | None        # the archived document, for Receipt Detail
    current: bool


class WorkshopContextOut(_Model):
    """What helps place a marked document: the week around it, and the batch it was scanned in."""

    week: list[ContextScanOut] | None   # None until the form has a receipt date
    batch_id: int | None
    batch: list[ContextScanOut]


class EnhancementIn(_Model):
    """How to prepare the scan before OCR reads it (the same values the preview endpoint takes)."""

    top_points: Literal["", "left", "right", "down"] = ""
    treatment: Literal["none", "clahe", "contrast", "whiten"] = "none"
    clip: float = Field(3.0, ge=1.0, le=10.0)
    grid: int = Field(8, ge=2, le=16)
    contrast: float = Field(2.5, ge=0.5, le=3.0)
    gamma: float = Field(0.5, ge=0.2, le=3.0)
    lightness: int = Field(200, ge=128, le=255)
    chroma: int = Field(10, ge=1, le=80)


class WorkshopReprocessIn(EnhancementIn):
    key: str
    ocr_model: str
    extractor: str


class WorkshopHintsIn(_Model):
    key: str
    draft: DraftIn


class WorkshopDecisionIn(_Model):
    """The workshop finishes a document: into the archive, or into ``tossed/``. Marking it again
    would leave it in a state the workshop itself can no longer reach."""

    key: str
    verdict: Literal["accepted", "tossed"]
    draft: DraftIn
    comment: str = ""


# --- curate: dedupe ----------------------------------------------------------------------
class DedupeMember(_Model):
    filename: str
    path: str
    name: str
    date: str
    time: str
    cost: float
    currency: str
    pages: int


class DedupeCluster(_Model):
    """Documents with the same cost within a few minutes of each other."""

    date: str
    time: str
    members: list[DedupeMember]


class KeptPair(_Model):
    """Two documents you said are different purchases, and their names for the undo list."""

    documents: list[str]
    names: list[str]


class DedupeOut(_Model):
    archived: int
    tossed: int
    clusters: list[DedupeCluster]
    kept: list[KeptPair]


class KeepIn(_Model):
    documents: list[str]


class TossIn(_Model):
    path: str


class TossOut(_Model):
    """Where the pages went, and the verdict they had — everything an undo needs."""

    tossed: list[str]
    previous_verdict: VerdictName
    name: str


class RestoreIn(_Model):
    paths: list[str]
    verdict: VerdictName


# --- curate: normalize -------------------------------------------------------------------
class NameGroupOut(_Model):
    """Names one engine thinks are the same shop, with how many documents carry each."""

    id: int
    names: list[str]
    counts: list[NameCount]
    canonical: list[str]


class NormalizeOut(_Model):
    engines: list[NormalizeEngineOut]
    engine: str
    threshold: float
    names: int
    groups: list[NameGroupOut]
    distinct_pairs: list[list[str]]


class MergeIn(_Model):
    target: str
    variants: list[str]


class MoveOut(_Model):
    source: str
    destination: str


class MergeOut(_Model):
    """What a merge would do (preview) or did. ``moves`` is every document that is re-filed."""

    target: str
    variants: list[str]
    documents: int
    moves: list[MoveOut]
    decisions: int
    smart_matches: int
    error: str | None


class DistinctIn(_Model):
    names: list[str]


class DistinctOut(_Model):
    pairs: int


# --- brand registry ----------------------------------------------------------------------
class BrandBranchOut(_Model):
    location: str
    receipts: int


class BrandPrefixOut(_Model):
    prefix: str
    receipts: int
    branches: list[BrandBranchOut]


class BrandOut(_Model):
    id: str
    label: str
    prefixes: list[str]
    receipt_count: int
    #: prefix -> branches, in the order the prefixes are written on the brand.
    tree: list[BrandPrefixOut]


class BrandOverview(_Model):
    receipts: int
    matched: int
    unmatched: int


class NameCount(_Model):
    name: str
    count: int


class BrandsOut(_Model):
    brands: list[BrandOut]
    overview: BrandOverview
    unmatched: list[NameCount]


class BrandIn(_Model):
    label: str
    prefixes: list[str]


class BrandPrefixIn(_Model):
    prefix: str


class PrefixSuggestionOut(_Model):
    prefix: str
    count: int
    names: list[str]


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
    #: What a shortcut can be besides one character (" " is Space), for the Config page's key recorder.
    shortcut_named_keys: list[str]


class PathCheck(_Model):
    """Whether a folder typed into the Config page exists (checked on the server, not the browser)."""

    path: str
    exists: bool
    is_dir: bool


# --- Dev: sanity check, index audit ------------------------------------------------------------------
class BatchSanityOut(_Model):
    batch_id: int
    archived: bool
    files: int
    organized: int
    #: an archived batch: its files the archive doesn't have
    missing_from_archive: list[str]
    #: a batch still being ingested: its files not in the scan folder (null when there is no scan folder)
    missing_from_input: list[str] | None


class SidecarMismatchOut(_Model):
    """An archive folder where a scan has no sidecar, or a sidecar has no scan."""

    folder: str
    missing_sidecar: list[str]
    extra_sidecar: list[str]


class SanityOut(_Model):
    indexed: bool                        # False until File Index has written batches.json
    batches: list[BatchSanityOut]
    sidecar_mismatches: list[SidecarMismatchOut]


class IndexFileOut(_Model):
    """batches.json itself. No creation time: every save writes a new file and swaps it in, so it would
    always equal the last change."""

    size: int
    modified: str


class DuplicateFilenameOut(_Model):
    filename: str
    count: int
    batch_ids: list[int]


class BatchStatOut(_Model):
    batch_id: int
    files: int
    running_total: int
    archived: bool
    start: str
    end: str


class InputComparisonOut(_Model):
    on_disk: int
    indexed_not_on_disk: int             # archived or moved: expected, so only counted
    on_disk_not_indexed: list[str]


class IndexAuditOut(_Model):
    index_file: IndexFileOut | None      # None until File Index has written batches.json
    total_batches: int
    archived: int
    non_archived: int
    total_entries: int
    unique_filenames: int
    lost_to_dedup: int
    duplicates: list[DuplicateFilenameOut]
    batches: list[BatchStatOut]
    input: InputComparisonOut | None     # None without a scan folder


# --- Dev: Experiment ---------------------------------------------------------------------------------
class ExperimentTreatmentIn(EnhancementIn):
    """The Workshop's treatments, plus denoising before and/or after them."""

    denoise_before: bool = False
    denoise_after: bool = False
    denoise_strength: int = Field(6, ge=3, le=15)


class ExperimentOcrIn(ExperimentTreatmentIn):
    model: str
    with_boxes: bool = True              # also run the grounding pass (models that have one)


class ExperimentParseIn(_Model):
    extractor: str
    custom_instruction: str = ""


class ExperimentPromptIn(_Model):
    custom_instruction: str = ""


class ExperimentPromptOut(_Model):
    prompt: str
    has_boxes: bool


class ExperimentOcrOut(_Model):
    model: str
    read_at: float                       # epoch seconds; changes with every run
    with_boxes: bool
    treatment: ExperimentTreatmentIn
    markdown: str
    structured_raw: str | None
    #: every box the grounding pass found (no fields: nothing has cited them yet)
    boxes: list[FieldBoxOut]
    seconds: float
    structured_seconds: float | None


class ExperimentParseOut(_Model):
    extractor: str
    custom_instruction: str
    extraction: DocumentExtraction
    field_boxes: list[FieldBoxOut]       # the boxes the extraction cites, as Review draws them
    seconds: float
    tokens: TokenUse | None              # what the call read and wrote; absent if the model doesn't say
    cost: float | None                   # dollars for this one call; None for a model that runs here


class PriceOut(_Model):
    """What a hosted model charges per million tokens."""

    input: float
    cached_input: float
    output: float


class ExperimentRunOut(_Model):
    id: str
    filename: str
    width: int
    height: int
    ocr: ExperimentOcrOut | None
    parse: ExperimentParseOut | None


class ExperimentOut(_Model):
    """The bench's options, and the run it was last used on."""

    ocr_models: list[str]
    grounding_models: list[str]          # the OCR models that can return boxes
    extractors: list[str]
    local_extractors: list[str]          # run on this machine's GPU, so they wait for OCR
    ocr_model: str
    extractor: str
    with_boxes: bool
    custom_instruction: str
    latest: str | None
    #: per extractor that bills by the token, so a run can be priced on the others too
    prices: dict[str, PriceOut]
