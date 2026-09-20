"""How fast a run may call a hosted model, worked out from the limits its responses report.

There is no asking what the limits are: every response carries them. From those and what the calls
themselves take -- how long they last, how many tokens they spend -- the pace follows: a limit of ``R``
requests a minute at ``L`` seconds a call supports ``R x L / 60`` calls at once, and the same arithmetic
against the token limit gives a second ceiling. A run aims at a fraction of both (``MARGIN``), because
the headers describe the moment a response was written and say nothing about the calls made since, and
because the limit belongs to the account rather than the run -- anything else spending it shows up here
as less room.

Being refused is not the plan; it means the reckoning was wrong somewhere, so the run halves what it has
in flight, waits as long as it was told, and climbs back a call at a time.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping

#: Aim for this much of the limit. The rest covers the calls in flight that no header has counted yet,
#: anything else on the account, and a limit that refills continuously while headers are a snapshot.
MARGIN = 0.7
#: The longest a run will sit waiting to be let back in. A per-minute limit comes round well inside
#: this; an hour-long refusal is a daily quota, and waiting it out is not something a run should do.
MAX_HOLD = 300
#: Where a run starts before any response has said what the limits are, and the most it will ever have
#: in flight: what a desktop and one archive can sensibly have outstanding, not what a limit allows.
START_SLOTS, MAX_SLOTS = 4, 32
#: Until a response says otherwise, assume nothing about the limits and let calls through.
UNKNOWN = -1

_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

log = logging.getLogger(__name__)


def duration(text: str | None) -> float | None:
    """``6m0s`` or ``120ms`` as seconds, the way the reset headers are written."""
    if not text:
        return None
    parts = _DURATION.findall(text.strip())
    return sum(float(amount) * _UNITS[unit] for amount, unit in parts) if parts else None


class RateBudget:
    """The limits as last reported, what the calls cost, and how many may be in flight because of it."""

    def __init__(self, margin: float = MARGIN, slots: int = START_SLOTS, ceiling: int = MAX_SLOTS) -> None:
        self._lock = threading.Condition()
        self._margin = margin
        self._ceiling = ceiling
        self.slots = slots                         # how many calls may be in flight now
        self.requests_limit = self.requests_left = UNKNOWN
        self.tokens_limit = self.tokens_left = UNKNOWN
        self._requests_reset_at = 0.0
        self._tokens_reset_at = 0.0
        self._hold_until = 0.0
        self._seconds: float | None = None         # how long a call has been taking
        self._tokens: float | None = None          # and what it has been spending
        self._in_flight = 0
        self._started_at: dict[int, float] = {}    # when each call in flight began, by thread
        self._next_start = 0.0                     # the earliest the next call may leave

    # --- what the provider says -----------------------------------------------------------------------
    def observe(self, headers: Mapping[str, str]) -> None:
        """Take the limits from a response. Headers it didn't send leave that count as it was."""
        now = time.monotonic()
        with self._lock:
            self.requests_limit = _number(headers, "x-ratelimit-limit-requests", self.requests_limit)
            self.requests_left = _number(headers, "x-ratelimit-remaining-requests", self.requests_left)
            self.tokens_limit = _number(headers, "x-ratelimit-limit-tokens", self.tokens_limit)
            self.tokens_left = _number(headers, "x-ratelimit-remaining-tokens", self.tokens_left)
            for header, attribute in (("x-ratelimit-reset-requests", "_requests_reset_at"),
                                      ("x-ratelimit-reset-tokens", "_tokens_reset_at")):
                seconds = duration(headers.get(header))
                if seconds is not None:
                    setattr(self, attribute, now + seconds)
            self._retarget(faster=False)

    def spent(self, tokens: int) -> None:
        """What a call used, which is the best guess at what the next one will use."""
        with self._lock:
            self._tokens = float(tokens) if self._tokens is None else (self._tokens * 3 + tokens) / 4
            if self.requests_left > 0:
                self.requests_left -= 1            # the next response will correct this
            if self.tokens_left > 0:
                self.tokens_left = max(0, self.tokens_left - tokens)
            self._retarget(faster=False)

    def rate_limited(self, retry_after: float | None) -> None:
        """Refused: hold every thread of the run, and halve what it keeps in flight.

        A wait longer than ``MAX_HOLD`` is capped here, but the caller is the one who decides whether
        such a wait is worth having at all -- see ``too_long_to_wait``.
        """
        with self._lock:
            wait = min(retry_after if retry_after is not None else 10.0, MAX_HOLD)
            self._hold_until = max(self._hold_until, time.monotonic() + wait)
            self.slots = max(1, self.slots // 2)
            log.info("rate limited: %d call(s) at a time now", self.slots)
            self._lock.notify_all()

    # --- what a run may do about it -------------------------------------------------------------------
    @staticmethod
    def too_long_to_wait(retry_after: float | None) -> bool:
        """Whether being asked to wait this long means the run should stop rather than hold."""
        return retry_after is not None and retry_after > MAX_HOLD

    def wait_for(self) -> float:
        """Seconds to sleep before the next call: 0 while there is room, until the reset when there isn't."""
        now = time.monotonic()
        with self._lock:
            reserve = (1 - self._margin) / 2       # half the margin: below this, stop rather than squeeze
            waits = [self._hold_until - now]
            if self.requests_limit > 0 and self.requests_left <= self.requests_limit * reserve:
                waits.append(self._requests_reset_at - now)
            if self.tokens_limit > 0:
                needed = self._tokens or 0.0
                if self.tokens_left - needed <= self.tokens_limit * reserve:
                    waits.append(self._tokens_reset_at - now)
            return max(0.0, *waits)

    def acquire(self, timeout: float = 0.25) -> bool:
        """Take a place among the calls in flight, or return False so the caller can notice a cancel.

        Two things have to be true: the run has a place free, and enough time has passed since the last
        call left. Short calls exhaust a limit while barely overlapping, so how often they go matters
        as much as how many are in the air at once.
        """
        with self._lock:
            if self._in_flight >= self.slots:
                self._lock.wait(timeout)
                if self._in_flight >= self.slots:
                    return False
            now = time.monotonic()
            if now < self._next_start:
                self._lock.wait(min(timeout, self._next_start - now))
                return False
            self._next_start = max(now, self._next_start) + self._interval()
            self._in_flight += 1
            self._started_at[threading.get_ident()] = time.monotonic()
            return True

    def release(self, ok: bool = True, seconds: float | None = None) -> None:
        """Give the slot back, with how long the call took -- half of what the pace is made of.

        The budget times the call itself unless the caller passes a figure it measured more precisely.
        """
        with self._lock:
            self._in_flight -= 1
            started = self._started_at.pop(threading.get_ident(), None)
            if ok and (seconds is not None or started is not None):
                seconds = seconds if seconds is not None else time.monotonic() - started
                self._seconds = seconds if self._seconds is None else (self._seconds * 3 + seconds) / 4
                self._retarget()
            self._lock.notify_all()

    # --- the arithmetic -------------------------------------------------------------------------------
    def target(self) -> int:
        """How many calls at once the limits allow at the pace calls are actually going at."""
        with self._lock:
            return self._target()

    def _target(self) -> int:
        seconds = self._seconds
        if seconds is None or seconds <= 0 or self.requests_limit <= 0:
            return self.slots                      # nothing measured yet: stay as we are
        # R requests a minute, each lasting L seconds, is R x L / 60 of them in flight at any moment.
        allowed = self.requests_limit * self._margin * seconds / 60
        if self.tokens_limit > 0 and self._tokens:
            allowed = min(allowed, (self.tokens_limit * self._margin / self._tokens) * seconds / 60)
        return max(1, min(self._ceiling, int(allowed)))

    def _interval(self) -> float:
        """The least time between one call leaving and the next, for the limits to hold over a minute."""
        per_minute = self.requests_limit * self._margin if self.requests_limit > 0 else 0.0
        if self.tokens_limit > 0 and self._tokens:
            by_tokens = self.tokens_limit * self._margin / self._tokens
            per_minute = min(per_minute, by_tokens) if per_minute else by_tokens
        return 60 / per_minute if per_minute > 0 else 0.0

    def _retarget(self, faster: bool = True) -> None:
        """Move towards what the limits allow: down to it at once, up a call per call that went through.

        Only a completed call earns a step up, so the pace rises at the speed evidence arrives rather
        than in jumps when a response happens to report more room.
        """
        wanted = self._target()
        if wanted == self.slots or (wanted > self.slots and not faster):
            return
        self.slots = self.slots + 1 if wanted > self.slots else wanted
        log.info("%d call(s) at a time now (the limits allow %d)", self.slots, wanted)
        self._lock.notify_all()


def _number(headers: Mapping[str, str], name: str, current: int) -> int:
    raw = headers.get(name)
    if raw is None:
        return current
    try:
        return int(float(raw))
    except ValueError:
        return current
