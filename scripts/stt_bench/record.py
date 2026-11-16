#!/usr/bin/env python
"""Record the benchmark sentences with the Mac's microphone (you read them aloud).

    python scripts/stt_bench/record.py                 # ~/voice-bench/
    python scripts/stt_bench/record.py --dir ~/noisy --start 5   # redo from sentence 5

For each sentence: it is shown, you press Enter, speak it, press Enter again.
Files are mono 16 kHz WAV (``01.wav`` ...) in the chosen folder, outside the
repository. Nothing is sent anywhere. macOS asks once whether this terminal may
use the microphone; that permission is yours to give.
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

RATE = 16_000
SENTENCES = Path(__file__).with_name("sentences_uz.json")


def main() -> int:
    import numpy as np
    import sounddevice as sd

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path.home() / "voice-bench")
    parser.add_argument("--start", type=int, default=1)
    args = parser.parse_args()
    sentences = json.loads(SENTENCES.read_text(encoding="utf-8"))
    args.dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(sentences)} sentences -> {args.dir}. Speak naturally, at normal distance.\n")
    for number, text in enumerate(sentences, 1):
        if number < args.start:
            continue
        while True:
            print(f"[{number}/{len(sentences)}]  {text}")
            input("   Enter, then speak... ")
            chunks: list = []

            def collect(data, _frames, _time, _status, sink=chunks) -> None:
                sink.append(data.copy())

            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", callback=collect)
            with stream:
                input("   (recording) Enter when done ")
            audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="int16")
            peak = int(np.abs(audio).max()) if audio.size else 0
            seconds = audio.size / RATE
            print(f"   {seconds:.1f} s, peak level {peak}")
            if seconds < 0.8 or peak < 800:
                again = input("   Very short or very quiet. Record again? [Y/n] ")
                if again.strip().lower() != "n":
                    continue
            break
        with wave.open(str(args.dir / f"{number:02d}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(audio.tobytes())
    print("Done. Run: python scripts/stt_bench/run.py --dir", args.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
