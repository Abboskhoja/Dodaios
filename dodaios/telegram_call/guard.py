"""Rules that decide whether the assistant may ring the owner at all.

Pure and clock-injectable. Nothing here knows Telegram: the caller asks
:meth:`CallGuard.check` before dialing and reports the outcome afterwards.

* only with the owner's explicit confirmation for this very call;
* one call at a time;
* a daily cap and a pause after every attempt, so there is no ring storm;
* a declined or hung-up call blocks any further ring for a long while.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["CallGuard", "CallOutcome", "CallRefused"]

DAY_S = 86_400.0


class CallOutcome:
    ANSWERED = "answered"
    MISSED = "missed"
    DECLINED = "declined"
    FAILED = "failed"


class CallRefused(Exception):
    """The guard said no. ``code`` is a stable word; ``retry_in_s`` may be 0."""

    def __init__(self, code: str, retry_in_s: float = 0.0) -> None:
        super().__init__(code)
        self.code = code
        self.retry_in_s = retry_in_s


@dataclass
class CallGuard:
    max_per_day: int = 3
    pause_s: float = 300.0
    declined_block_s: float = 1800.0
    clock: Callable[[], float] = time.time
    _active: bool = field(default=False, init=False)
    _attempts: list[float] = field(default_factory=list, init=False)
    _blocked_until: float = field(default=0.0, init=False)

    def check(self, *, confirmed: bool) -> None:
        """Raise :class:`CallRefused` unless a call may start now."""
        now = self.clock()
        if not confirmed:
            raise CallRefused("not_confirmed")
        if self._active:
            raise CallRefused("busy")
        if now < self._blocked_until:
            raise CallRefused("blocked", self._blocked_until - now)
        self._attempts = [t for t in self._attempts if now - t < DAY_S]
        if len(self._attempts) >= self.max_per_day:
            raise CallRefused("daily_limit", DAY_S - (now - self._attempts[0]))

    def begin(self) -> None:
        """Mark a call as started (call right after :meth:`check`)."""
        self._active = True
        self._attempts.append(self.clock())

    def end(self, outcome: str) -> None:
        """Mark the call over and apply the pause that follows ``outcome``."""
        self._active = False
        now = self.clock()
        if outcome == CallOutcome.DECLINED:
            self._blocked_until = now + self.declined_block_s
        elif outcome in (CallOutcome.MISSED, CallOutcome.FAILED, CallOutcome.ANSWERED):
            self._blocked_until = max(self._blocked_until, now + self.pause_s)

    @property
    def active(self) -> bool:
        return self._active
