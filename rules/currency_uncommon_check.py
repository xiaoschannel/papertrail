from models import DocumentExtraction, ReceiptResult
from validation import Hint

COMMON_CURRENCIES = {"JPY", "CNY"}


def currency_uncommon_check(extraction: DocumentExtraction) -> list[Hint]:
    if not isinstance(extraction, ReceiptResult):
        return []

    if not (extraction.currency or "").strip():
        return [Hint(message="Receipt has no currency set", color="#b8860b")]
    if extraction.currency.upper() not in COMMON_CURRENCIES:
        return [Hint(message=f"Uncommon currency: {extraction.currency}", color="#b8860b")]
    return []
