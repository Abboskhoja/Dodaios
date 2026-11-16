"""The call guard: confirmation, one at a time, pauses, daily cap, declined blocks."""

from __future__ import annotations

import pytest

from dodaios.telegram_call.guard import DAY_S, CallGuard, CallOutcome, CallRefused


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make(**kw) -> tuple[CallGuard, Clock]:
    clock = Clock()
    return CallGuard(clock=clock, **kw), clock


def refused(guard: CallGuard, **kw) -> CallRefused:
    with pytest.raises(CallRefused) as err:
        guard.check(**kw)
    return err.value


def test_needs_explicit_confirmation():
    guard, _ = make()
    assert refused(guard, confirmed=False).code == "not_confirmed"
    guard.check(confirmed=True)


def test_one_call_at_a_time():
    guard, _ = make()
    guard.check(confirmed=True)
    guard.begin()
    assert refused(guard, confirmed=True).code == "busy"


def test_pause_after_every_call_then_free():
    guard, clock = make(pause_s=300)
    guard.begin()
    guard.end(CallOutcome.ANSWERED)
    err = refused(guard, confirmed=True)
    assert err.code == "blocked" and 0 < err.retry_in_s <= 300
    clock.now += 301
    guard.check(confirmed=True)


def test_declined_call_blocks_much_longer():
    guard, clock = make(pause_s=300, declined_block_s=1800)
    guard.begin()
    guard.end(CallOutcome.DECLINED)
    clock.now += 600
    assert refused(guard, confirmed=True).code == "blocked"
    clock.now += 1300
    guard.check(confirmed=True)


def test_daily_cap_and_window_expiry():
    guard, clock = make(max_per_day=2, pause_s=0)
    for _ in range(2):
        guard.check(confirmed=True)
        guard.begin()
        guard.end(CallOutcome.MISSED)
    assert refused(guard, confirmed=True).code == "daily_limit"
    clock.now += DAY_S + 1
    guard.check(confirmed=True)
