"""Contract tests: Review endpoints against the mid-ingest fixture."""

import json

import pytest

from data import load_decisions, save_decisions
from models import ReviewDecision

CONFIRMED = "セブン-イレブン 品川駅前店（確認済）"


@pytest.fixture
def pending(configured_ingest, ingest_client):
    """Put 1:1, 1:3, 1:4-5 and 1:7 back in the queue.

    1:2 stays accepted under a confirmed spelling, so 1:1 (same extracted name) gets an exact smart
    match; 1:6 and 1:8 stay tossed. Page 1:1 cites its OCR boxes for name and cost.
    """
    decisions = load_decisions(configured_ingest)
    for key in ("1:1", "1:3", "1:4-5", "1:7"):
        del decisions[key]
    decisions["1:2"] = decisions["1:2"].model_copy(update={"name": CONFIRMED})
    save_decisions(configured_ingest, decisions)

    path = configured_ingest / "extractions.json"
    extractions = json.loads(path.read_text(encoding="utf-8"))
    extractions["1:1"]["field_sources"] = {"name": ["1:0"], "cost": ["1:1"], "currency": ["1:1"]}
    path.write_text(json.dumps(extractions, ensure_ascii=False), encoding="utf-8")
    return ingest_client


def _draft(**kw):
    base = dict(document_type="receipt", name="上海小笼包馆", date="2025-01-10", time="14:00", cost=520.0, currency="CNY")
    base.update(kw)
    return base


def test_queue_order_and_summary(pending, configured_ingest):
    body = pending.get("/api/review/queue").json()
    assert body["blocker"] is None
    assert [i["key"] for i in body["items"]] == ["1:7", "1:4-5", "1:1", "1:3"]
    assert body["items"][0] == {"key": "1:7", "document_type": "other", "label": "Business Card - John Doe"}
    summary = body["summary"]
    assert (summary["total"], summary["pending"]) == (7, 4)
    assert {v["verdict"]: v["count"] for v in summary["verdicts"]} == {"accepted": 1, "marked": 0, "tossed": 2}


def test_queue_blockers(ingest_client, configured_ingest):
    (configured_ingest / "extractions.json").unlink()
    assert pending_blocker(ingest_client) == "No extractions yet. Run Parse first."
    (configured_ingest / "batches.json").unlink()
    assert pending_blocker(ingest_client) == "Run File Index first to create batches.json."


def pending_blocker(client):
    return client.get("/api/review/queue").json()["blocker"]


def test_document_payload(pending):
    doc = pending.get("/api/review/document", params={"key": "1:1"}).json()
    assert doc["defaults"]["name"] == "セブン-イレブン 品川駅前店"
    assert doc["initial_name"] == CONFIRMED                     # exact smart match prefills the confirmed spelling
    top = doc["smart_matches"][0]
    assert (top["name"], top["quick_apply"], top["label"]) == (CONFIRMED, True, f"{CONFIRMED} — name 100%")
    assert doc["ocr_text"].startswith("--- Page 1 ---\nセブン-イレブン")
    assert doc["decision"] is None

    [page] = doc["pages"]
    assert (page["file_key"], page["filename"], page["image_available"]) == ("1:1", "01102025132642_1.png", True)
    assert [(b["index"], b["fields"]) for b in page["boxes"]] == [(0, ["name"]), (1, ["cost"])]
    assert page["boxes"][0]["rects"] == [{"x1": 80, "y1": 40, "x2": 880, "y2": 104}]


def test_multi_page_document_lists_every_page(pending):
    doc = pending.get("/api/review/document", params={"key": "1:4-5"}).json()
    assert [(p["file_key"], p["image_available"], p["boxes"]) for p in doc["pages"]] == [
        ("1:4", False, []), ("1:5", False, [])]
    assert "--- Page 2 ---" in doc["ocr_text"]


def test_document_404(pending):
    assert pending.get("/api/review/document", params={"key": "9:9"}).status_code == 404


def test_hints_and_name_status(pending):
    body = pending.post("/api/review/hints", json={"key": "1:3", "draft": _draft(cost=0.0, currency="")}).json()
    assert [h["message"] for h in body["hints"]][1:] == ["Receipt has no cost or cost is 0", "Receipt has no currency set"]
    assert body["name_status"] == "unseen"
    assert body["accept_error"] == "Receipt requires: currency"

    approved = pending.post("/api/review/hints", json={"key": "1:1", "draft": _draft(name=CONFIRMED)}).json()
    assert approved["name_status"] == "approved"
    assert approved["accept_error"] is None


def test_accept_validates_then_saves(pending, configured_ingest):
    bad = pending.post("/api/review/decisions", json={"key": "1:3", "verdict": "accepted", "draft": _draft(currency="")})
    assert bad.status_code == 422 and bad.json()["detail"] == "Receipt requires: currency"
    assert "1:3" not in load_decisions(configured_ingest)

    ok = pending.post("/api/review/decisions",
                      json={"key": "1:3", "verdict": "accepted", "draft": _draft(), "comment": "checked"})
    assert ok.status_code == 200 and ok.json()["pending"] == 3
    saved = load_decisions(configured_ingest)["1:3"]
    assert (saved.verdict, saved.cost, saved.currency, saved.comment) == ("accepted", 520.0, "CNY", "checked")


def test_mark_and_toss_are_never_blocked_and_non_receipts_drop_cost(pending, configured_ingest):
    marked = pending.post("/api/review/decisions",
                          json={"key": "1:3", "verdict": "marked", "draft": _draft(date="10/01/2025", cost=None)})
    assert marked.status_code == 200
    tossed = pending.post("/api/review/decisions", json={
        "key": "1:7", "verdict": "tossed",
        "draft": _draft(document_type="other", name="Card", cost=99.0, currency="JPY")})
    assert tossed.status_code == 200
    decisions = load_decisions(configured_ingest)
    assert (decisions["1:3"].verdict, decisions["1:3"].cost) == ("marked", 0.0)
    assert (decisions["1:7"].cost, decisions["1:7"].currency) == (0.0, "")


def test_decisions_are_read_fresh_so_other_writers_are_kept(pending, configured_ingest):
    # e.g. File Index tosses a document while the Review page is open
    decisions = load_decisions(configured_ingest)
    decisions["1:4-5"] = ReviewDecision(verdict="tossed", document_type="corrupted", name="", date="", time="")
    save_decisions(configured_ingest, decisions)

    pending.post("/api/review/decisions", json={"key": "1:3", "verdict": "accepted", "draft": _draft()})
    after = load_decisions(configured_ingest)
    assert after["1:4-5"].verdict == "tossed" and after["1:3"].verdict == "accepted"


def test_undo_returns_document_to_queue(pending, configured_ingest):
    made = {"key": "1:3", "verdict": "accepted", "draft": _draft(), "comment": "checked"}
    pending.post("/api/review/decisions", json=made)
    undone = pending.post("/api/review/undo", json=made)
    assert undone.status_code == 200 and undone.json()["pending"] == 4
    assert "1:3" not in load_decisions(configured_ingest)
    again = pending.post("/api/review/undo", json=made)
    assert again.status_code == 404 and "no longer has a decision" in again.json()["detail"]


def test_undo_takes_back_only_the_decision_it_made(pending, configured_ingest):
    """An older undo must not remove what came after it: the same document decided again, or (after a
    regroup renumbers the batch) another document now under the same key."""
    first = {"key": "1:3", "verdict": "accepted", "draft": _draft()}
    second = {"key": "1:7", "verdict": "tossed", "draft": _draft()}
    for made in (first, second):
        pending.post("/api/review/decisions", json=made)

    pending.post("/api/review/decisions", json={**first, "verdict": "marked"})     # decided again
    refused = pending.post("/api/review/undo", json=first)
    assert refused.status_code == 409 and "decided again or regrouped" in refused.json()["detail"]
    assert load_decisions(configured_ingest)["1:3"].verdict == "marked"            # the newer one stays

    decisions = load_decisions(configured_ingest)                                  # a regroup: 1:3 is now
    decisions["1:3"] = ReviewDecision(verdict="accepted", document_type="receipt",  # another receipt
                                      name="Another shop", date="2025-01-11", time="09:00", cost=80.0, currency="CNY")
    save_decisions(configured_ingest, decisions)
    assert pending.post("/api/review/undo", json={**first, "verdict": "marked"}).status_code == 409
    assert load_decisions(configured_ingest)["1:3"].name == "Another shop"

    assert pending.post("/api/review/undo", json=second).status_code == 200        # order doesn't matter
    assert "1:7" not in load_decisions(configured_ingest)


def test_clear_all(pending, configured_ingest):
    body = pending.delete("/api/review/decisions").json()
    assert body["pending"] == 7
    assert load_decisions(configured_ingest) == {}
    assert not list(configured_ingest.glob("decisions.*.tmp"))  # atomic save leaves no temp file


def test_decision_for_unknown_document_is_404(pending):
    r = pending.post("/api/review/decisions", json={"key": "9:9", "verdict": "tossed", "draft": _draft()})
    assert r.status_code == 404


def test_boxes_cited_on_a_later_page_of_a_multi_page_document(pending, configured_ingest):
    ocr_path = configured_ingest / "ocr" / "1.json"
    ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    ocr["1:5"]["boxes"] = [{"ref_type": "text", "coords": [[100, 300, 60, 260]], "text": "RM15.90"}]
    ocr_path.write_text(json.dumps(ocr, ensure_ascii=False), encoding="utf-8")
    ex_path = configured_ingest / "extractions.json"
    extractions = json.loads(ex_path.read_text(encoding="utf-8"))
    extractions["1:4-5"]["field_sources"] = {"cost": ["2:0"]}
    ex_path.write_text(json.dumps(extractions, ensure_ascii=False), encoding="utf-8")

    doc = pending.get("/api/review/document", params={"key": "1:4-5"}).json()
    first, second = doc["pages"]
    assert first["boxes"] == []
    assert second["boxes"] == [{"index": 0, "fields": ["cost"], "text": "RM15.90",
                                "rects": [{"x1": 60, "y1": 260, "x2": 100, "y2": 300}]}]


def test_document_needs_the_scan_index(pending, configured_ingest):
    (configured_ingest / "batches.json").unlink()
    r = pending.get("/api/review/document", params={"key": "1:1"})
    assert r.status_code == 409 and "File Index" in r.json()["detail"]
