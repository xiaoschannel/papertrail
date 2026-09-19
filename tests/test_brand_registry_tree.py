"""brand -> prefix -> branch, the tree the Manage section draws."""
import pandas as pd

from brand_registry import BrandDirectory, BrandEntry, brand_breakdown


def _records(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"document_type": kind, "name": name} for kind, name in rows])


DIRECTORY = BrandDirectory(brands=[
    BrandEntry(id="seven", label="Seven-Eleven", prefixes=["seven-eleven", "7-eleven"]),
    BrandEntry(id="tealive", label="Tealive", prefixes=["tealive", "teawhatever"]),
])


def test_breakdown_groups_branches_under_the_prefix_that_matched_them():
    records = _records([
        ("receipt", "Seven-Eleven KL"),
        ("receipt", "Seven-Eleven KL"),
        ("receipt", "Seven-Eleven Ampang"),
        ("receipt", "7-Eleven Puchong"),
        ("receipt", "Tealive PJ"),
        ("document", "Seven-Eleven Not A Receipt"),
    ])
    tree = brand_breakdown(records, DIRECTORY)

    seven = {node.prefix: node for node in tree["seven"]}
    assert [b.location for b in seven["seven-eleven"].branches] == ["KL", "Ampang"]   # busiest first
    assert [b.receipts for b in seven["seven-eleven"].branches] == [2, 1]
    assert seven["seven-eleven"].receipts == 3
    assert [b.location for b in seven["7-eleven"].branches] == ["Puchong"]
    # Only receipts count: the non-receipt document is not a branch of anything.
    assert sum(node.receipts for node in tree["seven"]) == 4


def test_breakdown_keeps_a_prefix_that_matches_nothing():
    """A dead prefix is the thing you most want to see when you are pruning a brand."""
    tree = brand_breakdown(_records([("receipt", "Tealive PJ")]), DIRECTORY)
    dead = next(node for node in tree["tealive"] if node.prefix == "teawhatever")
    assert dead.branches == []
    assert dead.receipts == 0


def test_breakdown_keeps_the_prefix_order_the_brand_is_written_in():
    tree = brand_breakdown(_records([("receipt", "7-Eleven Puchong")]), DIRECTORY)
    assert [node.prefix for node in tree["seven"]] == ["seven-eleven", "7-eleven"]


def test_a_name_that_is_only_the_prefix_has_an_empty_branch():
    tree = brand_breakdown(_records([("receipt", "Tealive")]), DIRECTORY)
    tealive = next(node for node in tree["tealive"] if node.prefix == "tealive")
    assert [(b.location, b.receipts) for b in tealive.branches] == [("", 1)]


def test_breakdown_of_an_empty_archive_still_lists_every_prefix():
    """Same rule as a dead prefix: the page shows what the brand claims, matched or not."""
    tree = brand_breakdown(pd.DataFrame(), DIRECTORY)
    assert [node.prefix for node in tree["seven"]] == ["seven-eleven", "7-eleven"]
    assert all(node.receipts == 0 and node.branches == [] for nodes in tree.values() for node in nodes)
