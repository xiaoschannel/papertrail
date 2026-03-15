from models import DocumentExtraction, ReceiptResult
from validation import Hint

COMMON_CURRENCIES = {"JPY", "CNY"}


def currency_uncommon_check(ext: DocumentExtraction) -> list[Hint]:
    if not isinstance(ext, ReceiptResult):
        return []

    if not (ext.currency or "").strip():
        return [Hint(message="Receipt has no currency set", color="#b8860b")]
    if ext.currency.upper() not in COMMON_CURRENCIES:
        return [Hint(message=f"Uncommon currency: {ext.currency}", color="#b8860b")]
    return []
