from models import DocumentExtraction, ReceiptResult
from validation import ValidationResult

COMMON_CURRENCIES = {"JPY", "CNY"}


def currency_uncommon_check(ext: DocumentExtraction) -> list[ValidationResult]:
    if not isinstance(ext, ReceiptResult):
        return []

    if ext.currency.upper() not in COMMON_CURRENCIES:
        return [ValidationResult(message=f"Uncommon currency: {ext.currency}", color="#b8860b")]
    return []
