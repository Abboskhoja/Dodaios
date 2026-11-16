#!/usr/bin/env python
"""Run spoken turns through the real STT -> brain -> TTS stack and time each stage.

    python scripts/voice_turn_bench.py --wav ~/voice-bench/*.wav   # your own recordings
    python scripts/voice_turn_bench.py --synthetic                  # TTS-made speech (NOT a real-user test)

One ``BrowserVoiceSession`` serves all turns in order, so the models load once
and the brain keeps the conversation (a follow-up like "what was my previous
question" works). Prints median and p95 per stage and the process's peak memory,
and writes the per-turn rows to ``--out`` as JSON. Recordings are mono 16-bit WAV;
other rates are resampled. Nothing is recorded or uploaded here beyond what the
configured providers already receive (a cloud STT/brain gets the audio/text).
"""

# ruff: noqa: ASYNC110, E501  (a polling bench script; long usage lines)
from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
import wave
from pathlib import Path

from dodaios.browser_voice.audio import STT_SAMPLE_RATE, resample_pcm16
from dodaios.browser_voice.session import BrowserVoiceSession
from dodaios.browser_voice.timing import summarize
from dodaios.core import config as cfg_mod

SYNTHETIC_PROMPTS = (
    "Salom, Jarvis, meni eshityapsanmi?",
    "Bugun nimalar qilamiz?",
    "O'zbek tilida Python haqida tushuntirib ber.",
    "Oldingi savolim nima edi?",
)
FRAME_MS = 20


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise SystemExit(f"{path}: need mono 16-bit WAV")
        return resample_pcm16(w.readframes(w.getnframes()), w.getframerate(), STT_SAMPLE_RATE)


async def synthetic(cfg: object, texts: tuple[str, ...]) -> list[bytes]:
    from dodaios.plugins.tts import build_tts_from_config

    tts = build_tts_from_config(cfg.tts)  # type: ignore[attr-defined]
    out = []
    for text in texts:
        pcm = bytearray()
        async for chunk in tts.synthesize(text, language_code="uz-UZ"):
            rate = int(getattr(chunk, "sample_rate", 24_000) or 24_000)
            pcm.extend(resample_pcm16(chunk.pcm, rate, STT_SAMPLE_RATE))
        out.append(bytes(pcm))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wav", nargs="*", type=Path, default=[])
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("voice_bench.json"))
    parser.add_argument("--silence-ms", type=int, default=700, help="end-of-turn silence")
    args = parser.parse_args()

    from dodaios.brain.factory import build_default_brain
    from dodaios.plugins.stt import build_stt_from_config
    from dodaios.plugins.tts import build_tts_from_config

    cfg = cfg_mod.load_config()
    if args.wav:
        clips = [read_wav(p) for p in sorted(args.wav)]
        label = "real recordings"
    elif args.synthetic:
        clips = await synthetic(cfg, SYNTHETIC_PROMPTS)
        label = "SYNTHETIC speech (not a real-user test)"
    else:
        parser.error("give --wav files or --synthetic")

    sent: list[bytes] = []

    async def send_binary(data: bytes) -> None:
        sent.append(data)

    async def send_json(_msg: dict) -> None:
        return None

    session = BrowserVoiceSession(
        session_id="bench",
        send_binary=send_binary,
        send_json=send_json,
        stt=build_stt_from_config(cfg.stt),
        brain=build_default_brain(bus=None, tier="router"),
        tts=build_tts_from_config(cfg.tts),
        browser_sample_rate=STT_SAMPLE_RATE,
        language_code="uz-UZ",
        config=cfg,
        owns_brain=True,
    )
    session._endpointer.silence_ms = args.silence_ms  # noqa: SLF001
    frame = STT_SAMPLE_RATE * FRAME_MS // 1000 * 2
    silence = b"\x00" * frame
    rows = []
    for index, clip in enumerate(clips, 1):
        before = len(session.timings)
        for i in range(0, len(clip), frame):
            await session.handle_audio_frame(clip[i : i + frame].ljust(frame, b"\x00"))
        for _ in range(args.silence_ms // FRAME_MS + 8):
            await session.handle_audio_frame(silence)
        deadline = time.monotonic() + 90
        while len(session.timings) == before and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        # Let the reply be fully AUDIBLE-done (not just sent: audio leaves faster than
        # real time), or the next utterance would be treated as a barge-in.
        while session._assistant_audible() and time.monotonic() < deadline:  # noqa: SLF001
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        if len(session.timings) > before:
            rows.append(session.timings[-1].stages())
            print(f"turn {index}: {rows[-1]}", flush=True)
        else:
            print(f"turn {index}: no reply within 90 s", flush=True)
    await session.end(reason="bench")

    summary = summarize(session.timings)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    print(
        f"\n{label}; {len(rows)}/{len(clips)} turns answered; end-of-turn silence {args.silence_ms} ms"
    )
    for name, stat in summary.items():
        print(
            f"  {name:22s} median {stat['median_ms']:8.0f} ms   p95 {stat['p95_ms']:8.0f} ms   n={stat['n']}"
        )
    print(f"  peak resident memory: {peak_mb:.0f} MB")
    args.out.write_text(json.dumps({"label": label, "turns": rows, "summary": summary}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
