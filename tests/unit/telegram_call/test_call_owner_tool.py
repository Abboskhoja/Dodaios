"""The call-owner tool: no target argument, refusals explained, nothing dialed unasked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dodaios.plugins.tool import call_owner as tool_mod
from dodaios.plugins.tool.call_owner import CallOwnerTool
from dodaios.telegram_call import runtime
from dodaios.telegram_call.caller import CallResult
from dodaios.telegram_call.guard import CallRefused


def cfg(enabled=True):
    return SimpleNamespace(
        integrations=SimpleNamespace(
            telegram_call=SimpleNamespace(enabled=enabled, owner_user_id=5),
            telegram=SimpleNamespace(allowed_user_ids=[5]),
        )
    )


class FakeCaller:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.kwargs = result, error, None

    async def call_owner(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.result


def patch(monkeypatch, caller, enabled=True):
    monkeypatch.setattr("dodaios.core.config.load_config", lambda: cfg(enabled))
    monkeypatch.setattr(runtime, "get_caller", lambda c=None: caller)


def test_the_schema_has_no_way_to_name_a_target():
    assert set(CallOwnerTool.schema["properties"]) == {"greeting"}
    assert CallOwnerTool.risk_tier == "ask"


@pytest.mark.asyncio
async def test_disabled_feature_dials_nothing(monkeypatch):
    caller = FakeCaller()
    patch(monkeypatch, caller, enabled=False)
    res = await CallOwnerTool().execute({}, None)
    assert not res.success and caller.kwargs is None


@pytest.mark.asyncio
async def test_approved_call_is_confirmed_and_reports_the_answer(monkeypatch):
    caller = FakeCaller(CallResult("answered", seconds=42))
    patch(monkeypatch, caller)
    res = await CallOwnerTool().execute({"greeting": "Salom"}, None)
    assert res.success and caller.kwargs == {"confirmed": True, "greeting": "Salom"}


@pytest.mark.asyncio
async def test_declined_call_is_a_failure_that_says_it_will_not_ring_again(monkeypatch):
    patch(monkeypatch, FakeCaller(CallResult("declined")))
    res = await CallOwnerTool().execute({}, None)
    assert not res.success and "not ringing again" in res.error


@pytest.mark.asyncio
async def test_refusals_are_explained(monkeypatch):
    patch(monkeypatch, FakeCaller(error=CallRefused("not_logged_in")))
    res = await CallOwnerTool().execute({}, None)
    assert "telegram_call_login.py" in res.error
    assert tool_mod._REFUSALS.keys() >= {"busy", "blocked", "daily_limit", "no_owner"}


def test_owner_is_the_configured_id_else_the_paired_owner():
    assert runtime.owner_id_of(cfg()) == 5
    other = cfg()
    other.integrations.telegram_call.owner_user_id = 0
    assert runtime.owner_id_of(other) == 5
    other.integrations.telegram.allowed_user_ids = []
    assert runtime.owner_id_of(other) == 0
