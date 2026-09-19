"""Review hint rules — the cues shown to the user during review."""

from datetime import datetime

from models import ReceiptItem, ReceiptResult
from rules.cost_check import cost_check
from rules.cost_large_check import cost_large_check
from rules.cost_zero_check import cost_zero_check
from rules.currency_uncommon_check import currency_uncommon_check
from rules.date_check import date_check

GREEN = "#28a745"
RED = "#dc3545"
AMBER = "#b8860b"


def _receipt(**kw) -> ReceiptResult:
    base = dict(document_type="receipt", language="ja", date="2025-01-10", time="13:00",
                name="Shop", phone="", currency="JPY", address="", items=[], cost=0.0)
    base.update(kw)
    return ReceiptResult(**base)


# --- cost_check ------------------------------------------------------------
def test_cost_check_matches_items_to_total():
    r = _receipt(currency="JPY", cost=1260.0,
                 items=[ReceiptItem(name="a", total_price=580.0), ReceiptItem(name="b", total_price=680.0)])
    hints = cost_check(r)
    assert len(hints) == 1 and hints[0].color == GREEN


def test_cost_check_flags_mismatch():
    r = _receipt(currency="CNY", cost=520.0, items=[ReceiptItem(name="a", total_price=500.0)])
    hints = cost_check(r)
    assert len(hints) == 1 and hints[0].color == RED
    assert "≠" in hints[0].message


def test_cost_check_noop_without_items():
    assert cost_check(_receipt(cost=100.0, items=[])) == []


# --- cost_large_check ------------------------------------------------------
def test_cost_large_check_fires_per_currency_threshold():
    assert cost_large_check(_receipt(currency="CNY", cost=520.0))[0].color == AMBER
    assert cost_large_check(_receipt(currency="JPY", cost=98000.0))[0].color == AMBER


def test_cost_large_check_below_threshold_and_unknown_currency():
    assert cost_large_check(_receipt(currency="JPY", cost=1260.0)) == []
    assert cost_large_check(_receipt(currency="MYR", cost=99999.0)) == []  # no threshold defined


# --- cost_zero_check -------------------------------------------------------
def test_cost_zero_check():
    assert cost_zero_check(_receipt(cost=0.0))[0].color == RED
    assert cost_zero_check(_receipt(cost=100.0)) == []


# --- currency_uncommon_check ----------------------------------------------
def test_currency_uncommon_check():
    assert currency_uncommon_check(_receipt(currency="MYR"))[0].color == AMBER
    assert currency_uncommon_check(_receipt(currency="JPY")) == []
    assert "no currency" in currency_uncommon_check(_receipt(currency="")).pop().message


# --- date_check (now injected) --------------------------------------------
def test_date_check_age_buckets():
    now = datetime(2025, 6, 1)
    assert date_check(_receipt(date="2025-01-01", time=""), now=now)[0].color == ""      # recent
    assert date_check(_receipt(date="2021-01-01", time=""), now=now)[0].color == AMBER   # 3-10y
    assert date_check(_receipt(date="2010-01-01", time=""), now=now)[0].color == RED     # >=10y
    assert date_check(_receipt(date="2030-01-01", time=""), now=now)[0].color == RED     # future


def test_date_check_unparseable():
    hints = date_check(_receipt(date="2025-99-99", time=""), now=datetime(2025, 6, 1))
    assert hints and hints[0].color == RED and "Failed to parse" in hints[0].message
