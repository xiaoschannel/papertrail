from models import DocumentExtraction, ReceiptResult
from validation import ValidationResult


def cost_zero_check(ext: DocumentExtraction) -> list[ValidationResult]:
    if not isinstance(ext, ReceiptResult):
        return []
    if ext.cost is None or ext.cost == 0:
        return [ValidationResult(message="Receipt has no cost or cost is 0", color="#dc3545")]
    return []
