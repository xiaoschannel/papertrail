"""Pure logic behind the Review step, extracted so the web API and tests share it.

Mirrors ``pages/ingest/review.py`` (Streamlit): queue order, form defaults, smart-match name prefill,
hint rules evaluated on the user's unsaved edits, accept validation, and which OCR boxes belong to which
extracted fields. No ``streamlit`` import and no disk access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Literal

from models import (
    CorruptedResult,
    DetectedBox,
    DocumentExtraction,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    SmartMatchCandidate,
)
from rules.cost_large_check import cost_large_check
from rules.cost_zero_check import cost_zero_check
from rules.currency_uncommon_check import currency_uncommon_check
from rules.date_check import date_check
from validation import Hint, is_date_time_safe_for_archive

DocumentType = Literal["receipt", "other", "corrupted"]
NameStatus = Literal["placeholder", "approved", "unseen"]

#: Names the form falls back to when extraction found none; never treated as a real merchant name.
PLACEHOLDER_NAMES = frozenset({"Receipt", "Document", "Corrupted"})

#: Extracted fields that are never drawn on the scan (see box_drawing.EXCLUDED_SOURCE_FIELDS).
EXCLUDED_SOURCE_FIELDS = frozenset({"document_type", "language", "currency", "items", "address", "phone"})


# --- queue --------------------------------------------------------------------
def review_sort_key(extraction: DocumentExtraction) -> tuple[int, str]:
    """Corrupted first, then other documents by title, then receipts by name (similar names adjacent)."""
    if isinstance(extraction, ReceiptResult):
        return (2, extraction.name.lower())
    if isinstance(extraction, OtherResult):
        return (1, (extraction.title or "").lower())
    return (0, "")


def pending_doc_keys(
    extractions: dict[str, DocumentExtraction], decisions: dict[str, ReviewDecision]
) -> list[str]:
    """Extracted documents without a decision yet, in review order."""
    pending = [k for k in extractions if k not in decisions]
    return sorted(pending, key=lambda k: review_sort_key(extractions[k]))


# --- form defaults ---------------------------------------------------------------
@dataclass(frozen=True)
class FormDefaults:
    document_type: DocumentType
    name: str
    date: str
    time: str
    cost: float
    currency: str
    phone: str


def form_defaults(extraction: DocumentExtraction) -> FormDefaults:
    """What the review form starts with for an extraction."""
    if isinstance(extraction, ReceiptResult):
        return FormDefaults("receipt", extraction.name or "Receipt", extraction.date, extraction.time,
                            extraction.cost, extraction.currency, extraction.phone)
    if isinstance(extraction, OtherResult):
        return FormDefaults("other", extraction.title or "Document", extraction.date, extraction.time, 0.0, "", "")
    return FormDefaults("corrupted", "Corrupted", "", "", 0.0, "", "")


def defaults_from_decision(decision) -> FormDefaults:
    """Form values for a document that has no extraction — all we know is what review recorded."""
    return FormDefaults(decision.document_type if decision.document_type in ("receipt", "other", "corrupted")
                        else "receipt", decision.name, decision.date, decision.time, decision.cost,
                        decision.currency, "")


def initial_name(default_name: str, candidates: list[SmartMatchCandidate]) -> str:
    """An exact smart-match hit replaces the extracted name with the previously confirmed spelling."""
    if candidates and abs(candidates[0].name_score - 1.0) < 1e-9:
        return candidates[0].confirmed_name
    return default_name


def name_status(name: str, confirmed_names: set[str]) -> NameStatus:
    if not name.strip() or name in PLACEHOLDER_NAMES:
        return "placeholder"
    return "approved" if name in confirmed_names else "unseen"


# --- hints on the draft ------------------------------------------------------------
@dataclass(frozen=True)
class Draft:
    """The review form's current values."""

    document_type: DocumentType
    name: str
    date: str
    time: str
    cost: float | None
    currency: str


def form_defaults_extraction(draft: "Draft") -> DocumentExtraction:
    """An empty extraction of the draft's type, for a document that has none to start from."""
    if draft.document_type == "receipt":
        return ReceiptResult(document_type="receipt", language="", date=draft.date, time=draft.time,
                             name=draft.name, currency=draft.currency, address="", cost=draft.cost or 0.0)
    if draft.document_type == "other":
        return OtherResult(document_type="other", language="", date=draft.date, time=draft.time, title=draft.name)
    return CorruptedResult(document_type="corrupted")


def draft_extraction(original: DocumentExtraction, draft: Draft) -> DocumentExtraction:
    """An extraction reflecting the draft, keeping the original's other fields when the type matches."""
    if draft.document_type == "receipt":
        src = original if isinstance(original, ReceiptResult) else None
        return ReceiptResult(
            document_type="receipt",
            language=src.language if src else "",
            date=draft.date,
            time=draft.time,
            name=draft.name,
            phone=src.phone if src else "",
            currency=draft.currency,
            address=src.address if src else "",
            items=src.items if src else [],
            cost=draft.cost or 0.0,
        )
    if draft.document_type == "other":
        src_other = original if isinstance(original, OtherResult) else None
        return OtherResult(
            document_type="other",
            language=src_other.language if src_other else "",
            date=draft.date,
            time=draft.time,
            title=draft.name,
        )
    return CorruptedResult(document_type="corrupted")


def hints_for(extraction: DocumentExtraction, now: datetime | None = None) -> list[Hint]:
    """The Review page's hint rules, in display order."""
    rules = [partial(date_check, now=now), cost_zero_check, cost_large_check, currency_uncommon_check]
    return [hint for rule in rules for hint in rule(extraction)]


# --- saving ------------------------------------------------------------------------------
def accept_error(draft: Draft) -> str | None:
    """Why the draft can't be accepted, or None. (Mark and Toss are never blocked.)"""
    if draft.document_type == "receipt":
        missing = [field for field, present in (("cost", draft.cost is not None),
                                               ("currency", bool(draft.currency.strip())))
                   if not present]
        if missing:
            return f"Receipt requires: {', '.join(missing)}"
    safe, err = is_date_time_safe_for_archive(draft.date, draft.time)
    return None if safe else err


# --- boxes on the scan ---------------------------------------------------------------------
@dataclass(frozen=True)
class FieldBox:
    """One OCR box on a page, with the extracted fields that cite it.

    ``rects`` are ``(x1, y1, x2, y2)`` on the OCR's 0-1000 scale relative to the page image, with
    corners normalized so x1 <= x2 and y1 <= y2 (OCR output doesn't guarantee the order).
    """

    index: int
    fields: tuple[str, ...]
    rects: tuple[tuple[int, int, int, int], ...]
    text: str | None


def field_boxes(page_num: int, boxes: list[DetectedBox], field_sources: dict[str, list[str]]) -> list[FieldBox]:
    """Boxes on ``page_num`` (1-based within the document) cited by extracted fields.

    Refs look like ``"page:box"``. Fields keep their ``field_sources`` order, so the first field of a
    box is the one that colors it; refs to boxes that don't exist are ignored.
    """
    cited: dict[int, list[str]] = {}
    for field, refs in field_sources.items():
        if field in EXCLUDED_SOURCE_FIELDS:
            continue
        for ref in refs:
            page, _, box = ref.partition(":")
            if page != str(page_num) or not box.isdigit():
                continue
            fields = cited.setdefault(int(box), [])
            if field not in fields:
                fields.append(field)
    out: list[FieldBox] = []
    for index in sorted(cited):
        if index >= len(boxes):
            continue
        rects = tuple(
            (min(c[0], c[2]), min(c[1], c[3]), max(c[0], c[2]), max(c[1], c[3]))
            for c in boxes[index].coords
            if len(c) == 4
        )
        if not rects:
            continue
        out.append(FieldBox(index=index, fields=tuple(cited[index]), rects=rects, text=boxes[index].text))
    return out
