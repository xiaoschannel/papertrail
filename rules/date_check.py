from datetime import datetime

import humanize

from models import CorruptedResult, DocumentExtraction
from validation import Hint


def date_check(extraction: DocumentExtraction) -> list[Hint]:
    if isinstance(extraction, CorruptedResult):
        return []
    if not extraction.date:
        return []

    time_fmt = "%H:%M:%S" if extraction.time and len(extraction.time) > 5 else "%H:%M"
    try:
        doc_dt = datetime.strptime(f"{extraction.date} {extraction.time}", f"%Y-%m-%d {time_fmt}") if extraction.time else datetime.strptime(extraction.date, "%Y-%m-%d")
    except ValueError:
        date_str = f"{extraction.date} {extraction.time}" if extraction.time else extraction.date
        return [Hint(message=f"Failed to parse: {date_str}", color="#dc3545")]

    now = datetime.now()
    humanized = humanize.naturaltime(doc_dt)
    delta_years = (now - doc_dt).total_seconds() / (365.25 * 24 * 3600)
    if doc_dt > now or delta_years >= 10:
        return [Hint(message=humanized, color="#dc3545")]
    elif delta_years > 3:
        return [Hint(message=humanized, color="#b8860b")]
    return [Hint(message=humanized, color="")]
