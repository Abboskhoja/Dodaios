"""Place one voice call from the assistant's Telegram account to its owner.

Everything external is injected, so the whole flow is testable without
Telegram: ``telethon_factory`` builds the MTProto client, ``calls_factory``
the PyTgCalls instance, ``session_factory`` the voice session, and
``read_secret`` the credential store.

Nothing signs in on the owner's behalf: without a stored session from the
owner's own login (``scripts/telegram_call_login.py``) every call is refused
with ``not_logged_in`` and no network is touched.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dodaios.telegram_call.bridge import CallAudioBridge
from dodaios.telegram_call.guard import CallGuard, CallOutcome, CallRefused

log = logging.getLogger("dodaios.telegram_call")

SECRET_API_HASH = "telegram_call_api_hash"  # noqa: S105 - the credential-store key name
SECRET_SESSION = "telegram_call_session"  # noqa: S105 - the credential-store key name

RATE = 48_000


@dataclass(frozen=True, slots=True)
class CallResult:
    outcome: str  # CallOutcome value
    seconds: float = 0.0
    turns: int = 0
    transcript: tuple[tuple[str, str], ...] = ()


class TelegramCaller:
    def __init__(
        self,
        *,
        cfg: Any,
        owner_user_id: int,
        guard: CallGuard,
        session_factory: Callable[..., Any],
        read_secret: Callable[[str], str | None],
        telethon_factory: Callable[[str, int, str], Any] | None = None,
        calls_factory: Callable[[Any], Any] | None = None,
        types_factory: Callable[[], Any] | None = None,
        greeting_synth: Callable[[str], Awaitable[bytes]] | None = None,
    ) -> None:
        self._cfg = cfg
        self._owner = int(owner_user_id)
        self._guard = guard
        self._session_factory = session_factory
        self._read_secret = read_secret
        self._telethon_factory = telethon_factory or _default_telethon
        self._calls_factory = calls_factory or _default_calls
        self._types_factory = types_factory or _call_types
        # Renders the greeting to 24 kHz PCM while the phone rings, so it is
        # heard the moment the owner picks up instead of after a synthesis wait.
        self._greeting_synth = greeting_synth
        self._lock = asyncio.Lock()

    # -- public ------------------------------------------------------------

    async def call_owner(self, *, confirmed: bool, greeting: str | None = None) -> CallResult:
        """Ring the owner once. Raises :class:`CallRefused` before any dialing."""
        if not self._owner:
            raise CallRefused("no_owner")
        if self._lock.locked():
            raise CallRefused("busy")
        async with self._lock:
            self._guard.check(confirmed=confirmed)
            api_hash = self._read_secret(SECRET_API_HASH) or ""
            session = self._read_secret(SECRET_SESSION) or ""
            api_id = int(getattr(self._cfg, "api_id", 0) or 0)
            if not (api_id and api_hash and session):
                raise CallRefused("not_logged_in")
            self._guard.begin()
            outcome = CallOutcome.FAILED
            try:
                result = await self._run(
                    api_id, api_hash, session, greeting or str(self._cfg.greeting)
                )
                outcome = result.outcome
                return result
            finally:
                self._guard.end(outcome)

    # -- one call ------------------------------------------------------------

    async def _run(self, api_id: int, api_hash: str, session: str, greeting: str) -> CallResult:
        client = self._telethon_factory(session, api_id, api_hash)
        calls = None
        bridge: CallAudioBridge | None = None
        started = asyncio.get_running_loop().time()
        hung_up = asyncio.Event()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise CallRefused("not_authorized")
            try:
                await client.get_input_entity(self._owner)
            except Exception as exc:  # noqa: BLE001 - map any resolution failure
                raise CallRefused("owner_unreachable") from exc

            calls = self._calls_factory(client)
            ct = self._types_factory()

            async def send_pcm(frame: bytes) -> None:
                await calls.send_frame(self._owner, ct.mic, frame)

            async def out_binary(data: bytes) -> None:
                if bridge is not None:
                    await bridge.send_binary(data)

            async def out_json(message: dict[str, Any]) -> None:
                if bridge is not None:
                    await bridge.send_json(message)

            session_obj = self._session_factory(send_binary=out_binary, send_json=out_json)
            bridge = CallAudioBridge(
                session=session_obj,
                send_pcm=send_pcm,
                rate=RATE,
                silence_timeout_s=float(self._cfg.silence_timeout_s),
                on_idle=lambda: _set(hung_up),
            )

            @calls.on_update(ct.frames_filter)
            async def _incoming(_client: Any, update: Any) -> None:
                if update.chat_id != self._owner:
                    return
                for frame in update.frames:
                    await bridge.feed(frame.frame)

            @calls.on_update(ct.chat_filter)
            async def _chat(_client: Any, update: Any) -> None:
                if update.chat_id == self._owner and update.status & ct.left_call:
                    hung_up.set()

            prefetch: asyncio.Task[bytes] | None = None
            if self._greeting_synth is not None and greeting:
                prefetch = asyncio.create_task(
                    self._greeting_synth(greeting), name="tgcall-greeting"
                )
            await calls.start()
            try:
                await calls.play(
                    self._owner, ct.stream(RATE), ct.call_config(int(self._cfg.ring_timeout_s))
                )
            except Exception as exc:  # noqa: BLE001 - classified by name below
                if prefetch is not None:
                    prefetch.cancel()
                return CallResult(_classify(exc))
            await calls.record(self._owner, ct.record_stream(RATE))
            greeting_pcm = b""
            if prefetch is not None:
                try:
                    greeting_pcm = await prefetch
                except Exception:  # noqa: BLE001 - fall back to speaking it live
                    log.debug("telegram call: greeting prefetch failed", exc_info=True)
            await bridge.start("" if greeting_pcm else greeting)
            if greeting_pcm:
                await bridge.send_binary(greeting_pcm)
            try:
                await asyncio.wait_for(hung_up.wait(), timeout=float(self._cfg.max_call_s))
            except TimeoutError:
                log.info("telegram call: maximum duration reached")
            return CallResult(
                CallOutcome.ANSWERED,
                seconds=asyncio.get_running_loop().time() - started,
                turns=len(bridge.transcript),
                transcript=tuple(bridge.transcript),
            )
        finally:
            if bridge is not None:
                await bridge.stop()
            if calls is not None:
                try:
                    await calls.leave_call(self._owner)
                except Exception:  # noqa: BLE001, S110 - already gone
                    pass
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001, S110
                pass


async def _set(event: asyncio.Event) -> None:
    event.set()


def _classify(exc: BaseException) -> str:
    """Map PyTgCalls' call errors onto outcomes by class name (no import needed)."""
    name = type(exc).__name__
    if name == "CallDeclined":
        return CallOutcome.DECLINED
    if name in {"TimedOutAnswer", "CallBusy"}:
        return CallOutcome.MISSED
    log.warning("telegram call failed: %s", name)
    return CallOutcome.FAILED


# -- the real libraries, imported only when a call is actually placed ----------


def _default_telethon(session: str, api_id: int, api_hash: str) -> Any:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(session), api_id, api_hash)


def _default_calls(client: Any) -> Any:
    from pytgcalls import PyTgCalls

    return PyTgCalls(client)


class _CallTypes:
    """The PyTgCalls names a call needs, resolved lazily."""

    def __init__(self) -> None:
        from pytgcalls import filters
        from pytgcalls.types import (
            CallConfig,
            ChatUpdate,
            Device,
            Direction,
            ExternalMedia,
            MediaStream,
            RecordStream,
        )
        from pytgcalls.types.raw import AudioParameters

        self.mic = Device.MICROPHONE
        self.left_call = ChatUpdate.Status.LEFT_CALL
        self.frames_filter = filters.stream_frame(Direction.INCOMING, Device.SPEAKER)
        self.chat_filter = filters.chat_update(ChatUpdate.Status.LEFT_CALL)
        self._cfg = CallConfig
        self._stream = (MediaStream, ExternalMedia, AudioParameters)
        self._record = (RecordStream, AudioParameters)

    def call_config(self, timeout: int) -> Any:
        return self._cfg(timeout=timeout)

    def stream(self, rate: int) -> Any:
        media_stream, external, params = self._stream
        return media_stream(
            external.AUDIO,
            audio_parameters=params(rate, 1),
            video_flags=media_stream.Flags.IGNORE,
        )

    def record_stream(self, rate: int) -> Any:
        record_stream, params = self._record
        return record_stream(audio=True, audio_parameters=params(rate, 1))


_types_cache: _CallTypes | None = None


def _call_types() -> _CallTypes:
    global _types_cache  # noqa: PLW0603
    if _types_cache is None:
        _types_cache = _CallTypes()
    return _types_cache
