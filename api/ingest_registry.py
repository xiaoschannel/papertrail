"""The OCR providers and extractors the pipeline can use, loaded on first use.

Importing ``ocr_providers`` imports torch and probes CUDA, so the API only does it when an ingest page
needs it; tests replace these functions with fakes.
"""

from __future__ import annotations

from collections.abc import Callable


def ocr_providers() -> dict[str, object]:
    from ocr_providers import OCR_PROVIDERS

    return OCR_PROVIDERS


def extractors() -> dict[str, Callable]:
    from extraction import EXTRACTORS

    return EXTRACTORS


def unload_extractor(name: str) -> Callable[[], None]:
    """How to free an extractor's model: Ollama models are unloaded; hosted APIs hold nothing locally."""
    if name.startswith("Ollama"):
        from extraction import unload_ollama

        return unload_ollama
    return lambda: None
