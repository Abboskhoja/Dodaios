"""The call audio bridge: framing, pacing, barge-in flush, downmix, idle timeout."""

from __future__ import annotations

import asyncio
import audioop
import math
import struct

import pytest

from dodaios.telegram_call.bridge import CallAudioBridge


class FakeSession:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.controls: list[dict] = []
        self.said: list[str] = []
        self.ended = ""

    async def handle_audio_frame(self, data: bytes) -> None:
        self.audio.append(data)

    async def handle_control(self, msg: dict) -> None:
        self.controls.append(msg)

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def end(self, reason: str = "") -> None:
        self.ended = reason


def tone(rate: int, ms: int, channels: int = 1) -> bytes:
    n = rate * ms // 1000
    mono = b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(n))
    return audioop.tostereo(mono, 2, 1, 1) if channels == 2 else mono


@pytest.mark.asyncio
async def test_start_announces_the_call_rate_and_language_and_greets():
    session = FakeSession()
    bridge = CallAudioBridge(session=session, send_pcm=lambda f: asyncio.sleep(0))
    await bridge.start("Salom!")
    await asyncio.sleep(0)
    assert session.controls == [{"type": "audio_start", "sample_rate": 48000, "language": "uz-UZ"}]
    assert session.said == ["Salom!"]
    await bridge.stop()
    assert session.ended == "hangup"


@pytest.mark.asyncio
async def test_incoming_frames_reach_the_session_and_stereo_is_downmixed():
    session = FakeSession()
    bridge = CallAudioBridge(session=session, send_pcm=lambda f: asyncio.sleep(0), channels=2)
    await bridge.feed(tone(48000, 20, channels=2))
    assert len(session.audio) == 1 and len(session.audio[0]) == 960 * 2
    await bridge.stop()


@pytest.mark.asyncio
async def test_assistant_audio_goes_out_as_paced_20ms_frames_at_the_call_rate():
    session, sent = FakeSession(), []

    async def send_pcm(frame: bytes) -> None:
        sent.append(frame)

    bridge = CallAudioBridge(session=session, send_pcm=send_pcm, frame_ms=20)
    await bridge.start()
    await bridge.send_binary(tone(24000, 200))  # 200 ms at 24 kHz
    for _ in range(40):
        await asyncio.sleep(0.01)
        if len(sent) >= 9:
            break
    await bridge.stop()
    assert len(sent) >= 9
    assert all(len(f) == 48000 * 20 // 1000 * 2 for f in sent)


@pytest.mark.asyncio
async def test_barge_in_flush_drops_queued_audio():
    session, sent = FakeSession(), []

    async def send_pcm(frame: bytes) -> None:
        sent.append(frame)

    async def slow_sleep(_s: float) -> None:
        await asyncio.sleep(0.02)

    bridge = CallAudioBridge(session=session, send_pcm=send_pcm, sleep=slow_sleep)
    await bridge.start()
    await bridge.send_binary(tone(24000, 2000))  # two seconds queued
    await asyncio.sleep(0.05)
    await bridge.send_json({"type": "tts_cancel"})
    queued_after = len(bridge._out)  # noqa: SLF001
    assert queued_after == 0
    count = len(sent)
    await asyncio.sleep(0.1)
    assert len(sent) - count <= 1  # at most the frame already in flight
    await bridge.stop()


@pytest.mark.asyncio
async def test_final_transcripts_are_kept_for_the_call_record():
    bridge = CallAudioBridge(session=FakeSession(), send_pcm=lambda f: asyncio.sleep(0))
    await bridge.send_json({"type": "transcript", "text": "Salom", "is_final": True})
    await bridge.send_json({"type": "transcript", "text": "...", "is_final": False})
    await bridge.send_json(
        {"type": "transcript", "text": "Eshitaman", "is_final": True, "role": "assistant"}
    )
    assert bridge.transcript == [("user", "Salom"), ("assistant", "Eshitaman")]
    await bridge.stop()


@pytest.mark.asyncio
async def test_silence_timeout_calls_on_idle_once():
    now = [0.0]
    idle: list[int] = []

    async def on_idle() -> None:
        idle.append(1)

    async def sleep(_s: float) -> None:
        now[0] += 30
        await asyncio.sleep(0)

    bridge = CallAudioBridge(
        session=FakeSession(),
        send_pcm=lambda f: asyncio.sleep(0),
        silence_timeout_s=60,
        on_idle=on_idle,
        clock=lambda: now[0],
        sleep=sleep,
    )
    await bridge.start()
    for _ in range(10):
        await asyncio.sleep(0)
    await bridge.stop()
    assert idle == [1]
