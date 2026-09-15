"""Review step logic: queue order, defaults, name prefill, draft hints, accept rules, field boxes."""

from datetime import datetime

import review_logic as rl
from data import load_extractions
from models import (
    CorruptedResult, DetectedBox, OtherResult, ReceiptItem, ReceiptResult, ReviewDecision, SmartMatchCandidate,
)


def _receipt(**kw) -> ReceiptResult:
    base = dict(document_type="receipt", language="ja", date="2025-01-10", time="13:26", name="Shop",
                phone="03-1234-5678", currency="JPY", address="東京都品川区サンプル町1-2-3",
                items=[ReceiptItem(name="お茶", total_price=150.0)], cost=150.0)
    base.update(kw)
    return ReceiptResult(**base)


def _decision(verdict="accepted", **kw) -> ReviewDecision:
    base = dict(verdict=verdict, document_type="receipt", name="x", date="", time="")
    base.update(kw)
    return ReviewDecision(**base)


def test_pending_queue_orders_corrupted_other_then_receipts_by_name(ingest_dir):
    extractions = load_extractions(ingest_dir)
    decisions = {"1:2": _decision(), "1:8": _decision("tossed")}
    # ranks: corrupted 1:6, other 1:7, receipts Tealive < セブン-イレブン < 上海 (lower-cased code points)
    assert rl.pending_doc_keys(extractions, decisions) == ["1:6", "1:7", "1:4-5", "1:1", "1:3"]


def test_form_defaults_per_type():
    r = rl.form_defaults(_receipt(name=""))
    assert (r.document_type, r.name, r.cost, r.currency, r.phone) == ("receipt", "Receipt", 150.0, "JPY", "03-1234-5678")
    o = rl.form_defaults(OtherResult(document_type="other", language="en", date="2025-01-01", time="", title=""))
    assert (o.document_type, o.name, o.cost, o.currency) == ("other", "Document", 0.0, "")
    c = rl.form_defaults(CorruptedResult(document_type="corrupted"))
    assert (c.document_type, c.name, c.date) == ("corrupted", "Corrupted", "")


def test_initial_name_takes_confirmed_spelling_only_on_exact_match():
    exact = SmartMatchCandidate(confirmed_name="セブン-イレブン", name_score=1.0, phone_score=0.0,
                                combined_score=1.0, quick_apply=True)
    close = exact.model_copy(update={"name_score": 0.9})
    assert rl.initial_name("セブンイレブン", [exact]) == "セブン-イレブン"
    assert rl.initial_name("セブンイレブン", [close]) == "セブンイレブン"
    assert rl.initial_name("セブンイレブン", []) == "セブンイレブン"


def test_name_status():
    confirmed = {"Tealive KLCC"}
    assert rl.name_status("", confirmed) == "placeholder"
    assert rl.name_status("Receipt", confirmed) == "placeholder"
    assert rl.name_status("Tealive KLCC", confirmed) == "approved"
    assert rl.name_status("Tealive Pavilion", confirmed) == "unseen"


def test_draft_extraction_keeps_original_details_only_for_same_type():
    original = _receipt()
    draft = rl.Draft(document_type="receipt", name="Edited", date="2025-02-01", time="", cost=None, currency="")
    live = rl.draft_extraction(original, draft)
    assert isinstance(live, ReceiptResult)
    assert (live.name, live.cost, live.phone, live.items) == ("Edited", 0.0, original.phone, original.items)

    as_other = rl.draft_extraction(original, rl.Draft("other", "Card", "2025-02-01", "", None, ""))
    assert isinstance(as_other, OtherResult) and as_other.title == "Card" and as_other.language == ""
    assert isinstance(rl.draft_extraction(original, rl.Draft("corrupted", "", "", "", None, "")), CorruptedResult)


def test_hints_for_draft_in_rule_order():
    now = datetime(2026, 1, 1)
    hints = rl.hints_for(_receipt(date="2030-01-01", cost=0.0, currency=""), now=now)
    messages = [h.message for h in hints]
    assert messages[1:] == ["Receipt has no cost or cost is 0", "Receipt has no currency set"]
    assert hints[0].color == "#dc3545"  # future date
    assert rl.hints_for(CorruptedResult(document_type="corrupted"), now=now) == []


def test_accept_error():
    ok = rl.Draft("receipt", "Shop", "2025-01-10", "13:26", 100.0, "JPY")
    assert rl.accept_error(ok) is None
    assert rl.accept_error(rl.Draft("receipt", "Shop", "", "", None, "")) == "Receipt requires: cost, currency"
    assert rl.accept_error(rl.Draft("receipt", "Shop", "", "", 0.0, "JPY")) is None  # zero is a hint, not a blocker
    assert rl.accept_error(rl.Draft("other", "Card", "2025/01/10", "", None, "")) == "Date must be YYYY-MM-DD (e.g. 2025-03-15)"
    assert rl.accept_error(rl.Draft("corrupted", "", "", "", None, "")) is None


def test_field_boxes_maps_refs_to_page_boxes():
    boxes = [
        DetectedBox(ref_type="title", coords=[[880, 104, 80, 40]], text="Shop"),  # corners reversed
        DetectedBox(ref_type="text", coords=[[80, 220, 470, 258]], text="合計 ¥150"),
    ]
    sources = {
        "name": ["1:0"],
        "cost": ["1:1", "2:0"],   # second ref is on another page
        "currency": ["1:1"],      # excluded from drawing
        "date": ["1:1", "1:9"],   # 1:9 doesn't exist
        "time": ["1:x"],          # malformed
    }
    out = rl.field_boxes(1, boxes, sources)
    assert [(b.index, b.fields) for b in out] == [(0, ("name",)), (1, ("cost", "date"))]
    assert out[0].rects == ((80, 40, 880, 104),)
    assert [b.index for b in rl.field_boxes(2, boxes, sources)] == [0]
    assert rl.field_boxes(1, boxes, {}) == []


def test_field_boxes_skips_boxes_without_a_usable_rectangle():
    boxes = [DetectedBox(ref_type="text", coords=[[1, 2, 3]], text="bad")]
    assert rl.field_boxes(1, boxes, {"name": ["1:0"]}) == []
