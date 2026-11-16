"""Sentence-streamed replies and per-turn timing marks (call / Mini App voice path)."""

from __future__ import annotations

import asyncio

import pytest

from dodaios.browser_voice.sentences import next_sentence_break
from dodaios.browser_voice.timing import TurnTimings, summarize
from tests.fakes.fake_telephony_stack import FakeSTT, FakeTTS
from tests.unit.browser_voice.test_session import (
    _drive_one_utterance,
    _make_session,
    _Sink,
    _SlowTTS,
)

pytestmark = pytest.mark.asyncio


# -- the sentence cutter ---------------------------------------------------


def test_first_sentence_is_cut_as_soon_as_it_is_whole():
    assert next_sentence_break("Salom! Qalaysiz", first=True) is None  # "Salom!" is too short alone
    buf = "Salom, qalaysiz? Yaxshimisiz"
    cut = next_sentence_break(buf, first=True)
    assert buf[:cut] == "Salom, qalaysiz?"


def test_short_fragments_wait_for_more_text():
    assert next_sentence_break("Ha. ", first=True) is None
    long = "Ha. Yaxshi, men sizni juda yaxshi tushundim. "
    assert next_sentence_break(long, first=False) is not None


def test_decimal_points_and_unfinished_text_are_not_breaks():
    assert next_sentence_break("Narx 3.5 dollar", first=True) is None
    assert next_sentence_break("Bu hali tugamagan gap", first=True) is None


def test_later_sentences_need_a_phrase_worth_of_text():
    assert next_sentence_break("Mayli. ", first=False) is None


# -- timing marks ----------------------------------------------------------


def test_stage_durations_and_missing_marks():
    t = TurnTimings()
    t.mark("speech_end", 10.0)
    t.mark("stt_start", 10.1)
    t.mark("stt_end", 11.1)
    t.mark("stt_end", 99.0)  # a repeat of a mark is ignored
    stages = t.stages()
    assert stages == {"handoff": 100.0, "stt": 1000.0}


def test_summary_has_median_p95_and_counts():
    turns = []
    for stt_ms in (100, 200, 300, 400, 1000):
        t = TurnTimings()
        t.mark("stt_start", 0.0)
        t.mark("stt_end", stt_ms / 1000)
        turns.append(t)
    out = summarize(turns)["stt"]
    assert out == {"median_ms": 300.0, "p95_ms": 1000.0, "n": 5}


# -- the streamed turn -----------------------------------------------------


class _SlowBrain:
    """Streams two sentences with a real pause between them, then finishes late."""

    def __init__(self) -> None:
        self._history: list[str] = []
        self.finished_at: float | None = None

    async def generate_stream(self, user_text: str, **_kw):
        yield "Salom, qalaysiz? "
        await asyncio.sleep(0.25)
        yield "Men yaxshiman, rahmat sizga."
        self.finished_at = asyncio.get_running_loop().time()


async def test_first_audio_leaves_before_the_brain_has_finished():
    sink = _Sink()
    first_audio: list[float] = []
    original = sink.send_binary

    async def stamped(data: bytes) -> None:
        if not first_audio:
            first_audio.append(asyncio.get_running_loop().time())
        await original(data)

    sink.send_binary = stamped  # type: ignore[method-assign]
    brain = _SlowBrain()
    session = _make_session(sink, brain=brain, stt=FakeSTT(["Salom"]), tts=FakeTTS(ms_per_char=1))
    await _drive_one_utterance(session)
    await asyncio.sleep(0.1)
    assert first_audio and brain.finished_at is not None
    assert first_audio[0] < brain.finished_at - 0.1
    assert session.turns == 1


async def test_each_sentence_is_synthesised_on_its_own():
    sink = _Sink()
    tts = _SlowTTS(chunks=2, delay=0.001)
    session = _make_session(sink, brain=_SlowBrain(), stt=FakeSTT(["Salom"]), tts=tts)
    await _drive_one_utterance(session)
    await asyncio.sleep(0.4)
    assert [t for t, _ in tts.calls] == ["Salom, qalaysiz?", "Men yaxshiman, rahmat sizga."]


async def test_turn_timing_is_recorded_and_sent():
    sink = _Sink()
    session = _make_session(
        sink, brain=_SlowBrain(), stt=FakeSTT(["Salom"]), tts=FakeTTS(ms_per_char=1)
    )
    await _drive_one_utterance(session)
    await asyncio.sleep(0.4)
    assert len(session.timings) == 1
    stages = session.timings[0].stages()
    assert {"stt", "brain_first_token", "tts_first_audio", "total_to_first_audio"} <= stages.keys()
    sent = [m for m in sink.json if m.get("type") == "turn_timing"]
    assert sent and sent[0]["stages_ms"]["stt"] >= 0


async def test_barge_in_during_a_streamed_reply_stops_the_audio():
    sink = _Sink()
    session = _make_session(
        sink, brain=_SlowBrain(), stt=FakeSTT(["Salom"]), tts=_SlowTTS(chunks=40, delay=0.02)
    )
    await _drive_one_utterance(session)
    await asyncio.sleep(0.05)
    await session._barge_in()  # noqa: SLF001
    count = len(sink.binary)
    await asyncio.sleep(0.2)
    assert len(sink.binary) - count <= 1
    assert any(m.get("type") == "tts_cancel" for m in sink.json)
