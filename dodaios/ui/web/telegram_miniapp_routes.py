"""The Telegram Mini App: a live voice call with the assistant inside Telegram.

Two public routes (the Telegram WebView carries no Dodaios credential):

* ``GET /tg/`` serves one static page (``miniapp/index.html``). It holds no
  secret; it opens the microphone, talks to ``/ws/audio`` and draws the call.
* ``POST /api/telegram/miniapp/session`` is the only way in. The page posts the
  signed ``initData`` Telegram gave it; the server verifies the signature with
  the bot token, checks the age and that the Telegram user is the owner, and
  answers with a single-use, 60-second WebSocket ticket for ``/ws/audio``.

Everything else on this app stays behind the normal boundary. A ticket opens
the same voice socket the desktop's own browser call uses, nothing more.
See ``docs/telegram-miniapp.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from dodaios.channels.telegram_miniapp import (
    InitDataError,
    RateLimiter,
    TokenStore,
    verify_init_data,
)
from dodaios.core import config as cfg_mod

log = logging.getLogger(__name__)

router = APIRouter(tags=["telegram-miniapp"])

MINIAPP_INDEX = Path(__file__).resolve().parent / "miniapp" / "index.html"

#: Every attempt, good or bad, and then failures alone: a guessing run is cut
#: off long before it matters (a signature is a 256-bit HMAC).
_ATTEMPTS = RateLimiter(limit=20, window_s=60.0)
_FAILURES = RateLimiter(limit=5, window_s=300.0)

#: A page session outlives Telegram's initData window so a dropped call can
#: reconnect; a hand-off code moves a call to Safari once, within a minute.
_PAGE_SESSIONS = TokenStore(ttl_s=4 * 3600.0, max_live=8)
_HANDOFFS = TokenStore(ttl_s=60.0, max_live=8)

_PAGE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    # The page loads nothing from anywhere but Telegram's own script and this
    # origin, and may only be framed by Telegram's clients.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'unsafe-inline' https://telegram.org; "
        "style-src 'unsafe-inline'; connect-src 'self' wss: ws:; img-src 'self' data:; "
        "media-src 'self' blob:; worker-src 'self' blob:; base-uri 'none'; "
        "form-action 'none'; frame-ancestors https://web.telegram.org "
        "https://webk.telegram.org https://webz.telegram.org"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class SessionBody(BaseModel):
    """Exactly one proof: Telegram's signed data, a page session or a hand-off."""

    init_data: str = Field(default="", max_length=8192, repr=False)
    session_token: str = Field(default="", max_length=128, repr=False)
    handoff: str = Field(default="", max_length=128, repr=False)


class HandoffBody(BaseModel):
    session_token: str = Field(default="", max_length=128, repr=False)


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})


def _telegram_state(request: Request) -> tuple[str, list[int]]:
    """The bot token and owner ids, read fresh so a new pairing counts at once."""
    cfg = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    if cfg is None:
        cfg = cfg_mod.load_config()
    telegram = cfg.integrations.telegram
    token = cfg_mod.get_secret("telegram_bot_token", "TELEGRAM_BOT_TOKEN") or ""
    return token, [int(i) for i in telegram.allowed_user_ids]


def _owner_is_paired(request: Request) -> bool:
    token, owners = _telegram_state(request)
    return bool(token and owners)


@router.get("/tg", include_in_schema=False, response_model=None)
@router.get("/tg/", include_in_schema=False, response_model=None)
def miniapp_page() -> FileResponse | JSONResponse:
    if not MINIAPP_INDEX.is_file():
        return _json({"detail": "The Telegram Mini App is not part of this build."}, 404)
    return FileResponse(str(MINIAPP_INDEX), headers=_PAGE_HEADERS, media_type="text/html")


def _mint_ticket_response(session_token: str) -> JSONResponse:
    from dodaios.ui.web.surface_security import _WS_TICKET_TTL_S, _mint_ws_ticket

    ticket = _mint_ws_ticket()
    if ticket is None:
        return _json({"error": "busy"}, 503)
    return _json(
        {"ticket": ticket, "expires_in": int(_WS_TICKET_TTL_S), "session_token": session_token}
    )


@router.post("/api/telegram/miniapp/session", operation_id="telegram_miniapp_session")
def miniapp_session(request: Request, body: SessionBody) -> JSONResponse:
    """Verify one proof of ownership and hand back a voice-socket ticket."""
    if _FAILURES.blocked() or not _ATTEMPTS.allow():
        return _json({"error": "rate_limited"}, 429)

    def refuse(code: str, status: int) -> JSONResponse:
        _FAILURES.record()
        log.info("telegram miniapp: session refused (%s)", code)
        return _json({"error": code}, status)

    if body.session_token:
        # A reconnect of a page that already proved itself.
        if not _owner_is_paired(request) or not _PAGE_SESSIONS.valid(body.session_token):
            return refuse("session_expired", 403)
        return _mint_ticket_response(body.session_token)

    if body.handoff:
        # Safari, one minute after Telegram's page minted the code.
        if not _owner_is_paired(request) or not _HANDOFFS.consume(body.handoff):
            return refuse("handoff_expired", 403)
    else:
        try:
            token, owners = _telegram_state(request)
            verify_init_data(body.init_data, token, owner_ids=owners)
        except InitDataError as exc:
            return refuse(exc.code, 503 if exc.code in {"no_token", "no_owner"} else 403)

    session_token = _PAGE_SESSIONS.mint()
    if session_token is None:
        return _json({"error": "busy"}, 503)
    log.info("telegram miniapp: owner session opened")
    return _mint_ticket_response(session_token)


@router.post("/api/telegram/miniapp/handoff", operation_id="telegram_miniapp_handoff")
def miniapp_handoff(request: Request, body: HandoffBody) -> JSONResponse:
    """A one-use code that opens the same call in Safari (``/tg/#h=<code>``)."""
    if _FAILURES.blocked() or not _ATTEMPTS.allow():
        return _json({"error": "rate_limited"}, 429)
    if not _owner_is_paired(request) or not _PAGE_SESSIONS.valid(body.session_token):
        _FAILURES.record()
        return _json({"error": "session_expired"}, 403)
    code = _HANDOFFS.mint()
    if code is None:
        return _json({"error": "busy"}, 503)
    return _json({"code": code, "expires_in": 60})
