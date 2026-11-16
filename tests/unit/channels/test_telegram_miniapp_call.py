"""Mini App end to end through the real boundary: initData -> ticket -> /ws/audio.

A strangers' request must never reach the voice socket; the owner's signed
initData must, over a public HTTPS host, with no cookie and no Bearer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dodaios.browser_voice.route import router as voice_router
from dodaios.channels.telegram_miniapp import RateLimiter
from dodaios.ui.web import surface_security
from dodaios.ui.web import telegram_miniapp_routes as routes
from dodaios.ui.web.surface_security import SurfaceSecurity
from tests.unit.channels.test_telegram_miniapp import BOT_TOKEN, NOW, OWNER, signed

# The boundary must be tested without the suite-wide authenticated client.
pytestmark = pytest.mark.no_auto_web_auth

PUBLIC = "https://jarvis-call.example.net"


class _Session:
    def __init__(self, send_json):
        self.send_json = send_json
        self.audio: list[bytes] = []
        self.ended = False

    async def handle_audio_frame(self, data: bytes) -> None:
        self.audio.append(bytes(data))

    async def handle_control(self, msg: dict) -> None:
        if msg.get("type") == "audio_start":
            await self.send_json({"type": "audio_ready"})

    async def end(self, reason: str = "") -> None:
        self.ended = True


@pytest.fixture
def client(monkeypatch):
    surface_security.reset_ws_tickets()
    routes._PAGE_SESSIONS.clear()
    routes._HANDOFFS.clear()
    routes._ATTEMPTS = RateLimiter(50, 60.0)
    routes._FAILURES = RateLimiter(50, 300.0)
    cfg = SimpleNamespace(
        integrations=SimpleNamespace(telegram=SimpleNamespace(allowed_user_ids=[OWNER])),
        voice=SimpleNamespace(mode="pipeline"),
        browser_voice=SimpleNamespace(enabled=True),
        tts=SimpleNamespace(volume=1.0),
    )
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *a, **k: BOT_TOKEN)
    monkeypatch.setattr("dodaios.channels.telegram_miniapp.time.time", lambda: NOW)
    app = FastAPI()
    app.state.config = cfg
    app.state.bus = None
    sessions: list[_Session] = []

    def factory(*, session_id, send_binary, send_json):
        sessions.append(_Session(send_json))
        return sessions[-1]

    app.state.browser_voice_session_factory = factory
    # Pipeline mode with no realtime provider falls through to the classic factory.
    monkeypatch.setattr(
        "dodaios.realtime.factory.build_realtime_session", lambda **_: None, raising=False
    )
    app.include_router(routes.router)
    app.include_router(voice_router)
    secured = SurfaceSecurity(app, public_urls=(PUBLIC,))
    tc = TestClient(secured, base_url=PUBLIC, client=("203.0.113.9", 40000))
    tc.sessions = sessions  # type: ignore[attr-defined]
    return tc


def _headers():
    return {"Origin": PUBLIC}


def test_stranger_cannot_open_the_voice_socket(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/audio", headers=_headers()) as ws:
            ws.receive_json()
    assert client.sessions == []


def test_session_endpoint_is_reachable_without_a_credential_but_refuses_strangers(client):
    res = client.post(
        "/api/telegram/miniapp/session", json={"init_data": signed(user_id=7)}, headers=_headers()
    )
    assert res.status_code == 403


def test_owner_talks_through_the_ticket(client):
    res = client.post(
        "/api/telegram/miniapp/session", json={"init_data": signed()}, headers=_headers()
    )
    assert res.status_code == 200
    ticket = res.json()["ticket"]
    with client.websocket_connect(
        f"wss://jarvis-call.example.net/ws/audio?ticket={ticket}", headers=_headers()
    ) as ws:
        ws.send_json({"type": "audio_start", "sample_rate": 48000, "language": "uz-UZ"})
        frames = [ws.receive_json(), ws.receive_json()]
        assert "audio_ready" in [f["type"] for f in frames]
        ws.send_bytes(b"\x01\x00" * 160)
        ws.send_json({"type": "audio_stop"})
    assert client.sessions[0].audio == [b"\x01\x00" * 160]
    # The ticket is single use.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"wss://jarvis-call.example.net/ws/audio?ticket={ticket}", headers=_headers()
        ) as ws:
            ws.receive_json()


def test_other_api_routes_stay_closed_to_the_page(client):
    assert client.get("/api/config", headers=_headers()).status_code in (401, 403, 404)
    assert client.post("/api/ui/ws-ticket", headers=_headers()).status_code in (401, 403)


def test_page_is_public_and_reconnects_use_the_page_session(client):
    assert client.get("/tg/", headers=_headers()).status_code == 200
    first = client.post(
        "/api/telegram/miniapp/session", json={"init_data": signed()}, headers=_headers()
    ).json()
    again = client.post(
        "/api/telegram/miniapp/session",
        json={"session_token": first["session_token"]},
        headers=_headers(),
    )
    assert again.status_code == 200 and again.json()["ticket"] != first["ticket"]
