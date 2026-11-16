"""Audio bridge between one Telegram call and one assistant voice session.

    owner's voice -> NTgCalls frames (s16le @ ``rate``) -> feed() -> BrowserVoiceSession
        (VAD -> STT -> brain -> TTS, barge-in) -> 24 kHz PCM -> resample -> 20 ms paced frames
        -> ``send_pcm`` -> NTgCalls -> the owner's phone

It reuses the browser voice session unchanged, so turn detection, barge-in and
the per-call conversation (one brain per session) are the ones the Mini App and
desktop already have. Telegram's own echo cancellation keeps the assistant's
voice out of the owner's microphone, so no extra echo gate is added here.
"""

from __future__ import annotations

import asyncio
import audioop
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from dodaios.browser_voice.audio import TTS_SAMPLE_RATE, Resampler

log = logging.getLogger("dodaios.telegram_call.bridge")

SendPcm = Callable[[bytes], Awaitable[None]]
_SAMPLE_WIDTH = 2


class CallAudioBridge:
    def __init__(
        self,
        *,
        session: Any,
        send_pcm: SendPcm,
        rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        silence_timeout_s: float = 60.0,
        on_idle: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session = session
        self._send_pcm = send_pcm
        self.rate = rate
        self._channels = channels
        self._frame_bytes = rate * frame_ms // 1000 * _SAMPLE_WIDTH
        self._frame_s = frame_ms / 1000
        self._silence_timeout_s = silence_timeout_s
        self._on_idle = on_idle
        self._clock = clock
        self._sleep = sleep
        self._out = bytearray()
        self._resampler = Resampler(TTS_SAMPLE_RATE, rate)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = False
        self._last_activity = clock()
        #: What was said, in order: ``(role, text)`` for the call's record.
        self.transcript: list[tuple[str, str]] = []
        self.frames_in = 0
        self.frames_out = 0

    # -- lifecycle -------------------------------------------------------

    async def start(self, greeting: str = "", *, language: str = "uz-UZ") -> None:
        await self._session.handle_control(
            {"type": "audio_start", "sample_rate": self.rate, "language": language}
        )
        self._last_activity = self._clock()
        self._tasks.append(asyncio.create_task(self._pace(), name="tgcall-pace"))
        self._tasks.append(asyncio.create_task(self._watch_idle(), name="tgcall-idle"))
        if greeting:
            self._tasks.append(
                asyncio.create_task(self._session.say(greeting), name="tgcall-greet")
            )

    async def stop(self, reason: str = "hangup") -> None:
        if self._stopped:
            return
        self._stopped = True
        self._out.clear()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110 - teardown
                pass
        await self._session.end(reason=reason)

    # -- the owner's voice in ----------------------------------------------

    async def feed(self, frame: bytes) -> None:
        """One raw frame from the call: s16le at ``rate`` with ``channels`` channels."""
        if self._stopped or not frame:
            return
        if self._channels == 2:
            frame = audioop.tomono(frame, _SAMPLE_WIDTH, 0.5, 0.5)
        elif self._channels != 1:
            return
        self.frames_in += 1
        await self._session.handle_audio_frame(frame)

    # -- the assistant's voice out (the session's two sinks) ---------------------

    async def send_binary(self, pcm24k: bytes) -> None:
        if self._stopped or not pcm24k:
            return
        self._out.extend(self._resampler.process(pcm24k))
        self._last_activity = self._clock()
        self._wake.set()

    async def send_json(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type", ""))
        if kind in {"tts_cancel", "tts_end_flush"}:
            self.flush()
        elif kind == "transcript" and message.get("is_final"):
            role = str(message.get("role") or "user")
            self.transcript.append((role, str(message.get("text", ""))))
            self._last_activity = self._clock()
        elif kind in {"tts_start", "thinking"}:
            self._last_activity = self._clock()

    def flush(self) -> None:
        """Drop queued assistant audio (a barge-in) so it stops within one frame."""
        self._out.clear()
        self._resampler.reset()

    # -- pacing and idle ------------------------------------------------------

    async def _pace(self) -> None:
        """Send queued audio in real time, one frame per tick, drift-corrected."""
        next_at = self._clock()
        while not self._stopped:
            if len(self._out) < self._frame_bytes:
                self._wake.clear()
                await self._wake.wait()
                next_at = self._clock()
                continue
            frame = bytes(self._out[: self._frame_bytes])
            del self._out[: self._frame_bytes]
            try:
                await self._send_pcm(frame)
            except Exception:  # noqa: BLE001 - one lost frame must not end the call
                log.debug("telegram call: frame send failed", exc_info=True)
            self.frames_out += 1
            next_at += self._frame_s
            delay = next_at - self._clock()
            if delay > 0:
                await self._sleep(delay)
            else:
                next_at = self._clock()  # fell behind: do not burst to catch up

    async def _watch_idle(self) -> None:
        while not self._stopped:
            await self._sleep(1.0)
            if self._clock() - self._last_activity >= self._silence_timeout_s:
                log.info("telegram call: silence timeout")
                if self._on_idle is not None:
                    await self._on_idle()
                return
