from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from models import DocumentExtraction


# --- Hints (non-blocking cues for the user) ---


class Hint(BaseModel):
    message: str
    color: str


class HintRule(Protocol):
    def __call__(self, ext: DocumentExtraction) -> list[Hint]: ...


# --- Submission blockers (must pass before save) ---


def is_date_time_safe_for_archive(date: str, time: str) -> tuple[bool, str]:
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return False, "Date must be YYYY-MM-DD (e.g. 2025-03-15)"
    if time:
        parts = time.split(":")
        if not all(p.isdigit() for p in parts):
            return False, "Time must be HH:MM or HH:MM:SS"
        if len(parts) > 3:
            return False, "Time must be HH:MM or HH:MM:SS"
    return True, ""
