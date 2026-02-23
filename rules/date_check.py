from datetime import datetime

import humanize

from models import CorruptedResult, DocumentExtraction
from validation import ValidationResult


def date_check(ext: DocumentExtraction) -> list[ValidationResult]:
    if isinstance(ext, CorruptedResult):
        return []
    if not ext.date:
        return []

    time_fmt = "%H:%M:%S" if ext.time and len(ext.time) > 5 else "%H:%M"
    try:
        doc_dt = datetime.strptime(f"{ext.date} {ext.time}", f"%Y-%m-%d {time_fmt}") if ext.time else datetime.strptime(ext.date, "%Y-%m-%d")
    except ValueError:
        date_str = f"{ext.date} {ext.time}" if ext.time else ext.date
        return [ValidationResult(message=f"Failed to parse: {date_str}", color="#dc3545")]

    now = datetime.now()
    humanized = humanize.naturaltime(doc_dt)
    delta_years = (now - doc_dt).total_seconds() / (365.25 * 24 * 3600)
    if doc_dt > now or delta_years >= 10:
        return [ValidationResult(message=humanized, color="#dc3545")]
    elif delta_years > 3:
        return [ValidationResult(message=humanized, color="#b8860b")]
    return [ValidationResult(message=humanized, color="")]
