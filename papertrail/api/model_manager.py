"""At most one ML model loaded at a time, so switching models never stacks them in VRAM.

Every model the pipeline uses (DeepSeek OCR 2 in this process; glm-ocr and qwen3 inside Ollama) is
registered here by name with a function that unloads it. ``acquire`` unloads whatever else is loaded
*before* the caller loads its model; ``release`` unloads the current one, and jobs call it when they
finish, fail or are cancelled.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class ModelManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._name: str | None = None
        self._unload: Callable[[], None] | None = None

    @property
    def loaded(self) -> str | None:
        return self._name

    def acquire(self, name: str, unload: Callable[[], None]) -> None:
        """Make ``name`` the only model allowed to be loaded (unloading any other first)."""
        with self._lock:
            if self._name == name:
                return
            self._unload_current()
            self._name, self._unload = name, unload

    def release(self) -> None:
        """Unload the current model, if any."""
        with self._lock:
            self._unload_current()

    def _unload_current(self) -> None:
        if self._unload is not None:
            try:
                self._unload()
            except Exception:  # a failed unload must not block loading the next model
                log.exception("unloading %s failed", self._name)
        self._name, self._unload = None, None


models = ModelManager()
