from models import DocumentExtraction, ReceiptResult
from validation import Hint


def cost_zero_check(extraction: DocumentExtraction) -> list[Hint]:
    if not isinstance(extraction, ReceiptResult):
        return []
    if extraction.cost is None or extraction.cost == 0:
        return [Hint(message="Receipt has no cost or cost is 0", color="#dc3545")]
    return []
