"""Time+cost duplicate detection (5-minute window, exact cost match)."""

from datetime import datetime

import data
import dedupe_candidates as dd


def test_parse_verdict_datetime_valid_and_invalid():
    assert dd.parse_verdict_datetime("2025-01-10", "13:26:42") == datetime(2025, 1, 10, 13, 26)
    assert dd.parse_verdict_datetime("2025-01-10", "13:26") == datetime(2025, 1, 10, 13, 26)
    assert dd.parse_verdict_datetime("", "13:26") is None
    assert dd.parse_verdict_datetime("2025-01-10", "") is None
    assert dd.parse_verdict_datetime("2025/01/10", "13:26") is None
    assert dd.parse_verdict_datetime("2025-01-10", "ab:cd") is None


def test_find_dedupe_clusters_pairs_equal_cost_within_window(ingest_dir):
    decisions = data.load_decisions(ingest_dir)
    clusters = dd.find_dedupe_clusters(decisions)
    # 1:1 (13:26) and 1:2 (13:30) are accepted receipts, equal 1260, <5 min apart.
    assert clusters == [["1:1", "1:2"]]


def test_find_dedupe_ignores_tossed_and_non_receipts(ingest_dir):
    decisions = data.load_decisions(ingest_dir)
    clustered = {fn for cluster in dd.find_dedupe_clusters(decisions) for fn in cluster}
    assert "1:6" not in clustered  # tossed corrupted
    assert "1:7" not in clustered  # 'other'
    assert "1:8" not in clustered  # tossed receipt


def test_find_dedupe_separates_on_cost():
    from models import ReviewDecision

    base = dict(verdict="accepted", document_type="receipt", name="X",
                date="2025-01-10", time="10:00:00", currency="JPY")
    decisions = {
        "a": ReviewDecision(**base, cost=100.0),
        "b": ReviewDecision(**{**base, "time": "10:01:00"}, cost=100.0),
        "c": ReviewDecision(**{**base, "time": "10:02:00"}, cost=999.0),
    }
    # a,b,c are inside one 5-min time cluster, but only a,b share a cost.
    assert dd.find_dedupe_clusters(decisions) == [["a", "b"]]
