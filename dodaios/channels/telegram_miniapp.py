"""Server-side checks for the Telegram Mini App (the live voice page).

A Telegram Mini App opened from an inline button receives ``initData``: a query
string Telegram signs with a key derived from the bot token. Nothing the page
says about itself is trusted; the server recomputes the signature, checks how
old it is and then compares the user id with the owner allowlist.

Signature scheme (Telegram, "Validating data received via the Mini App"):

    secret_key = HMAC_SHA256(key="WebAppData", message=bot_token)
    data_check_string = "\\n".join(sorted("key=value" for every field but hash))
    hash == hex(HMAC_SHA256(key=secret_key, message=data_check_string))

The bot token is read by the caller from the credential store and is never
logged, returned or placed in an exception message here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Iterable
from urllib.parse import parse_qsl

__all__ = [
    "InitDataError",
    "RateLimiter",
    "TokenStore",
    "verify_init_data",
]

#: ``initData`` older than this is refused (a replayed or leaked string).
DEFAULT_MAX_AGE_S = 3600
#: A little clock skew between Telegram and this machine is tolerated.
_FUTURE_SKEW_S = 60
_MAX_INIT_DATA_CHARS = 8192


class InitDataError(ValueError):
    """``initData`` is missing, forged, stale or not the owner's.

    ``code`` is a stable machine word the page maps to a sentence; the message
    never carries the token, the hash or the raw input.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _hash_of(bot_token: str, data_check_string: str) -> str:
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    owner_ids: Iterable[int],
    max_age_s: int = DEFAULT_MAX_AGE_S,
    now: float | None = None,
) -> int:
    """Return the verified owner's Telegram user id, or raise :class:`InitDataError`.

    Codes: ``no_token`` (bot not connected), ``no_owner`` (nobody is paired
    yet), ``malformed``, ``bad_signature``, ``stale``, ``not_owner``.
    """
    if not bot_token:
        raise InitDataError("no_token")
    owners = {int(i) for i in owner_ids}
    if not owners:
        raise InitDataError("no_owner")
    if not init_data or len(init_data) > _MAX_INIT_DATA_CHARS:
        raise InitDataError("malformed")

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.pop("hash", "")
    if not received:
        raise InitDataError("malformed")
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    if not hmac.compare_digest(_hash_of(bot_token, check_string), received):
        raise InitDataError("bad_signature")

    # Only now is the content authentic; parse it.
    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError):
        raise InitDataError("malformed") from None
    clock = time.time() if now is None else now
    if auth_date > clock + _FUTURE_SKEW_S or clock - auth_date > max_age_s:
        raise InitDataError("stale")
    if user_id not in owners:
        raise InitDataError("not_owner")
    return user_id


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` hits per ``window_s`` seconds."""

    def __init__(self, limit: int, window_s: float) -> None:
        self._limit = limit
        self._window_s = window_s
        self._hits: deque[float] = deque()

    def _trim(self, clock: float) -> None:
        while self._hits and clock - self._hits[0] >= self._window_s:
            self._hits.popleft()

    def blocked(self, now: float | None = None) -> bool:
        """Whether the limit is reached, without recording a hit."""
        clock = time.monotonic() if now is None else now
        self._trim(clock)
        return len(self._hits) >= self._limit

    def record(self, now: float | None = None) -> None:
        """Count one hit."""
        self._hits.append(time.monotonic() if now is None else now)

    def allow(self, now: float | None = None) -> bool:
        """Count a hit and say whether it is within the limit."""
        clock = time.monotonic() if now is None else now
        if self.blocked(clock):
            return False
        self._hits.append(clock)
        return True


class TokenStore:
    """In-memory random tokens with a lifetime and a cap on how many are live.

    Used for the Mini App's page-session tokens (so a reconnect does not depend
    on Telegram's ``initData`` age) and the one-use hand-off codes that move a
    call from Telegram's WebView to Safari. Nothing is written to disk and only
    the process that minted a token can accept it.
    """

    def __init__(self, ttl_s: float, max_live: int) -> None:
        self._ttl_s = ttl_s
        self._max_live = max_live
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for token in [t for t, expiry in self._tokens.items() if expiry < now]:
            del self._tokens[token]

    def mint(self, now: float | None = None) -> str | None:
        """A fresh token, or ``None`` when too many are live."""
        clock = time.monotonic() if now is None else now
        with self._lock:
            self._purge(clock)
            if len(self._tokens) >= self._max_live:
                return None
            token = secrets.token_urlsafe(32)
            self._tokens[token] = clock + self._ttl_s
            return token

    def valid(self, token: str, now: float | None = None) -> bool:
        """Whether ``token`` is live (it stays live)."""
        clock = time.monotonic() if now is None else now
        with self._lock:
            self._purge(clock)
            return bool(token) and token in self._tokens

    def consume(self, token: str, now: float | None = None) -> bool:
        """Spend ``token`` once; a replay or an expired token fails."""
        clock = time.monotonic() if now is None else now
        with self._lock:
            self._purge(clock)
            return bool(token) and self._tokens.pop(token, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
