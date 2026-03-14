from models import DocumentExtraction, ReceiptResult
from validation import Hint

LARGE_COST_THRESHOLDS: dict[str, float] = {
    "JPY": 10_000,
    "CNY": 450,
}


def cost_large_check(ext: DocumentExtraction) -> list[Hint]:
    if not isinstance(ext, ReceiptResult) or ext.cost is None:
        return []

    currency = ext.currency.upper()
    threshold = LARGE_COST_THRESHOLDS.get(currency)
    if threshold is None:
        return []

    if ext.cost >= threshold:
        is_jpy = currency == "JPY"
        fmt = f"¥{ext.cost:,.0f}" if is_jpy else f"{ext.cost:,.2f} {ext.currency}"
        return [Hint(message=f"Large cost: {fmt} (≥ {threshold:,.0f} {currency})", color="#b8860b")]
    return []
