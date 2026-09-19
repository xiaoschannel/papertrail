"""Response models for the visualize/analytics endpoints (config uses ``settings.AppConfig``).

Endpoints declare these as ``response_model``, so responses are validated at runtime. FastAPI turns
them into the OpenAPI schema; ``tools/export_openapi.py`` snapshots it to ``frontend/openapi.json``
and ``openapi-typescript`` generates the frontend's types (``frontend/src/api/schema.d.ts``) from that.

Shapes mirror what the pure core already produces (``viz_records``, ``analytics``), serialized by
``api.serialization``: timestamps are ISO strings, missing values are ``null``. Every field is
required and ``extra="forbid"``, so a column added to or dropped from a frame fails response
validation in the API tests instead of silently drifting from the generated types.
"""

from __future__ import annotations

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


class MerchantProfile(_Model):
    metrics: MerchantMetrics
    trend: list[MonthlySpend]
    cadence: list[CadencePoint]
    items: list[ItemBreakdownRow]
    receipts: list[GalleryReceipt]


class DateRange(_Model):
    min: str | None
    max: str | None


class Health(_Model):
    status: str
