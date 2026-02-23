from models import DocumentExtraction, ReceiptResult
from validation import ValidationResult


def cost_check(ext: DocumentExtraction) -> list[ValidationResult]:
    if not isinstance(ext, ReceiptResult) or not ext.items:
        return []

    items_with_total = [item for item in ext.items if item.total_price is not None]
    if not items_with_total:
        return []

    is_jpy = ext.currency.upper() == "JPY"
    fmt = (lambda v: f"¥{v:,.0f}") if is_jpy else (lambda v: f"{v:,.2f} {ext.currency}")
    items_sum = sum(item.total_price for item in items_with_total)
    if abs(items_sum - ext.cost) < 0.01:
        return [ValidationResult(message=f"Items sum {fmt(items_sum)} = Total {fmt(ext.cost)}", color="#28a745")]
    diff = ext.cost - items_sum
    return [ValidationResult(message=f"Items sum {fmt(items_sum)} ≠ Total {fmt(ext.cost)} (diff: {fmt(diff)})", color="#dc3545")]
