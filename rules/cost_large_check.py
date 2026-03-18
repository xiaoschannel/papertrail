from models import DocumentExtraction, ReceiptResult
from validation import Hint

LARGE_COST_THRESHOLDS: dict[str, float] = {
    "JPY": 10_000,
    "CNY": 450,
}


def cost_large_check(extraction: DocumentExtraction) -> list[Hint]:
    if not isinstance(extraction, ReceiptResult) or extraction.cost is None:
        return []

    currency = extraction.currency.upper()
    threshold = LARGE_COST_THRESHOLDS.get(currency)
    if threshold is None:
        return []

    if extraction.cost >= threshold:
        is_jpy = currency == "JPY"
        fmt = f"¥{extraction.cost:,.0f}" if is_jpy else f"{extraction.cost:,.2f} {extraction.currency}"
        return [Hint(message=f"Large cost: {fmt} (≥ {threshold:,.0f} {currency})", color="#b8860b")]
    return []
