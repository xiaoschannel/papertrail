from models import DocumentExtraction, ReceiptResult
from validation import Hint


def cost_check(extraction: DocumentExtraction) -> list[Hint]:
    if not isinstance(extraction, ReceiptResult) or not extraction.items:
        return []

    items_with_total = [item for item in extraction.items if item.total_price is not None]
    if not items_with_total:
        return []

    is_jpy = extraction.currency.upper() == "JPY"
    fmt = (lambda v: f"¥{v:,.0f}") if is_jpy else (lambda v: f"{v:,.2f} {extraction.currency}")
    items_sum = sum(item.total_price for item in items_with_total)
    if abs(items_sum - extraction.cost) < 0.01:
        return [Hint(message=f"Items sum {fmt(items_sum)} = Total {fmt(extraction.cost)}", color="#28a745")]
    diff = extraction.cost - items_sum
    return [Hint(message=f"Items sum {fmt(items_sum)} ≠ Total {fmt(extraction.cost)} (diff: {fmt(diff)})", color="#dc3545")]
