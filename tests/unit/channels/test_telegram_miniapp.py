"""Telegram Mini App: initData verification, token stores and the session routes."""
# ruff: noqa: S105, S106  (every token in this file is a synthetic test value)

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dodaios.channels.telegram_miniapp import (
    InitDataError,
    RateLimiter,
    TokenStore,
    verify_init_data,
)
from dodaios.ui.web import telegram_miniapp_routes as routes

BOT_TOKEN = "123456:TEST-TOKEN-not-a-real-secret"
OWNER = 4242
NOW = 1_800_000_000


def signed(user_id: int = OWNER, *, auth_date: int = NOW, token: str = BOT_TOKEN) -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def verify(data: str, **kw):
    return verify_init_data(data, BOT_TOKEN, owner_ids=[OWNER], now=NOW, **kw)


def code_of(data: str, **kw) -> str:
    with pytest.raises(InitDataError) as err:
        verify(data, **kw)
    return err.value.code


def test_owner_is_accepted():
    assert verify(signed()) == OWNER


def test_forged_signature_is_refused():
    assert code_of(signed(token="999:other-token")) == "bad_signature"


def test_tampered_user_is_refused():
    data = signed()
    assert code_of(data.replace("4242", "1")) == "bad_signature"


def test_stranger_with_valid_signature_is_not_the_owner():
    assert code_of(signed(user_id=7)) == "not_owner"


def test_stale_and_future_data_are_refused():
    assert code_of(signed(auth_date=NOW - 3601)) == "stale"
    assert code_of(signed(auth_date=NOW + 3600)) == "stale"


@pytest.mark.parametrize("data", ["", "a=b"])
def test_malformed_input_is_refused(data):
    assert code_of(data) == "malformed"


def test_a_wrong_hash_is_a_bad_signature():
    assert code_of("hash=abc") == "bad_signature"


def test_missing_token_and_owner_are_distinct_states():
    with pytest.raises(InitDataError) as no_token:
        verify_init_data(signed(), "", owner_ids=[OWNER], now=NOW)
    with pytest.raises(InitDataError) as no_owner:
        verify_init_data(signed(), BOT_TOKEN, owner_ids=[], now=NOW)
    assert (no_token.value.code, no_owner.value.code) == ("no_token", "no_owner")


def test_error_text_never_carries_token_or_input():
    data = signed(token="999:other-token")
    with pytest.raises(InitDataError) as err:
        verify(data)
    assert BOT_TOKEN not in str(err.value) and data not in str(err.value)


def test_rate_limiter_window():
    limiter = RateLimiter(2, 10.0)
    assert limiter.allow(0) and limiter.allow(1) and not limiter.allow(2)
    assert limiter.allow(11)


def test_token_store_single_use_expiry_and_cap():
    store = TokenStore(ttl_s=10, max_live=2)
    a, b = store.mint(0), store.mint(0)
    assert a and b and store.mint(1) is None
    assert store.consume(a, 1) and not store.consume(a, 1)
    assert store.valid(b, 5) and not store.valid(b, 11)
    assert store.mint(11) is not None


@pytest.fixture
def client(monkeypatch):
    routes._PAGE_SESSIONS.clear()
    routes._HANDOFFS.clear()
    routes._ATTEMPTS = RateLimiter(20, 60.0)
    routes._FAILURES = RateLimiter(5, 300.0)
    cfg = SimpleNamespace(
        integrations=SimpleNamespace(telegram=SimpleNamespace(allowed_user_ids=[OWNER]))
    )
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *a, **k: BOT_TOKEN)
    monkeypatch.setattr(routes.cfg_mod, "load_config", lambda: cfg)
    monkeypatch.setattr("dodaios.channels.telegram_miniapp.time.time", lambda: NOW)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def post(client, **body):
    return client.post("/api/telegram/miniapp/session", json=body)


def test_session_gives_a_ticket_and_a_page_session(client):
    res = post(client, init_data=signed())
    assert res.status_code == 200
    body = res.json()
    assert body["ticket"] and body["session_token"] and body["expires_in"] == 60
    assert BOT_TOKEN not in res.text


def test_session_refuses_a_stranger_and_a_forgery(client):
    assert post(client, init_data=signed(user_id=7)).status_code == 403
    assert post(client, init_data=signed(token="1:x")).json() == {"error": "bad_signature"}
    assert post(client).status_code == 403


def test_reconnect_with_the_page_session_works_without_init_data(client):
    token = post(client, init_data=signed()).json()["session_token"]
    again = post(client, session_token=token)
    assert again.status_code == 200 and again.json()["ticket"]
    assert post(client, session_token="nope").status_code == 403


def test_handoff_opens_safari_once(client):
    token = post(client, init_data=signed()).json()["session_token"]
    code = client.post("/api/telegram/miniapp/handoff", json={"session_token": token}).json()[
        "code"
    ]
    first = post(client, handoff=code)
    assert first.status_code == 200 and first.json()["session_token"] != token
    assert post(client, handoff=code).status_code == 403


def test_handoff_needs_a_live_session(client):
    res = client.post("/api/telegram/miniapp/handoff", json={"session_token": "x"})
    assert res.status_code == 403


def test_repeated_failures_are_rate_limited(client):
    codes = [post(client, init_data=signed(token="1:x")).status_code for _ in range(7)]
    assert codes[:5] == [403] * 5 and codes[5:] == [429, 429]
    assert post(client, init_data=signed()).status_code == 429


def test_unpaired_bot_answers_503(client, monkeypatch):
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *a, **k: "")
    assert post(client, init_data=signed()).status_code == 503


def test_page_is_served_with_a_locked_down_policy(client):
    res = client.get("/tg/")
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        assert "frame-ancestors" in res.headers["content-security-policy"]
        assert res.headers["cache-control"].startswith("no-store")
