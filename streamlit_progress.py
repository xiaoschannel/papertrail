import time

import streamlit as st


class ProgressBar:
    def __init__(self, total: int) -> None:
        self._total = total
        self._done = 0
        self._failed = 0
        self._start = time.time()
        self._bar = st.progress(0)
        self._status = st.empty()

    def tick(self, succeeded: bool = True) -> None:
        if not succeeded:
            self._failed += 1
        self._done += 1
        elapsed = time.time() - self._start
        avg = elapsed / self._done
        remaining = (self._total - self._done) * avg
        mins, secs = divmod(int(remaining), 60)
        self._bar.progress(self._done / self._total)
        parts = [f"{self._done}/{self._total}", f"{avg:.1f}s/item", f"ETA: {mins}m {secs}s"]
        if self._failed:
            parts.append(f"{self._failed} failed")
        self._status.text(" — ".join(parts))
