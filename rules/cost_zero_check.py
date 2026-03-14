from models import DocumentExtraction, ReceiptResult
from validation import Hint


def cost_zero_check(ext: DocumentExtraction) -> list[Hint]:
    if not isinstance(ext, ReceiptResult):
        return []
    if ext.cost is None or ext.cost == 0:
        return [Hint(message="Receipt has no cost or cost is 0", color="#dc3545")]
    return []
