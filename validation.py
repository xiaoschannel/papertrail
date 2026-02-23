from typing import Protocol

from pydantic import BaseModel

from models import DocumentExtraction


class ValidationResult(BaseModel):
    message: str
    color: str


class ValidationRule(Protocol):
    def __call__(self, ext: DocumentExtraction) -> list[ValidationResult]: ...
