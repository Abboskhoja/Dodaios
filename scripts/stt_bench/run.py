#!/usr/bin/env python
"""Compare speech-to-text engines on your recorded Uzbek sentences.

    python scripts/stt_bench/run.py --dir ~/voice-bench --engines gemini fwhisper
    python scripts/stt_bench/run.py --dir ~/voice-bench --engines onnx --onnx-model gigaam-multilingual-ctc

Engines (each is skipped with a reason if its package or model is missing; none
is downloaded here):
  gemini    the configured Gemini key, model --gemini-model (audio leaves the Mac)
  fwhisper  faster-whisper on CPU, checkpoint --fw-model (name or local CT2 folder)
  onnx      onnx-asr, e.g. GigaAM Multilingual, --onnx-model [--onnx-int8]
  mlx       mlx-whisper, --mlx-model (HF repo or folder)

Reports per engine: mean WER and CER against the known sentences, per-clip
latency median and p95 (model load excluded, one warm-up clip first), load time,
peak memory added by the engine, and how many answers came back in Cyrillic.
The accuracy/speed balance is decided from this table, not from speed alone.
"""

# ruff: noqa: E501  (long usage lines in the docstring and table)
from __future__ import annotations

import argparse
import asyncio
import json
import resource
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import cer, has_cyrillic, wer  # noqa: E402

SENTENCES = Path(__file__).with_name("sentences_uz.json")
RATE = 16_000


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def load_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return w.readframes(w.getnframes())


def write_tmp_wav(pcm: bytes) -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(handle, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return handle.name


class Gemini:
    def __init__(self, model: str) -> None:
        from dodaios.plugins.stt.gemini_api import GeminiSTT

        self.name = f"gemini:{model}"
        self._stt = GeminiSTT(model=model, language="uz")

    def transcribe(self, pcm: bytes) -> str:
        out = self._stt.transcribe_pcm(pcm, 16000)
        out = asyncio.run(out) if asyncio.iscoroutine(out) else out
        return getattr(out, "text", out) or ""


class FWhisper:
    def __init__(self, model: str) -> None:
        from faster_whisper import WhisperModel

        self.name = f"fwhisper:{Path(model).name}"
        self._model = WhisperModel(model, device="cpu", compute_type="int8")

    def transcribe(self, pcm: bytes) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio, language="uz", beam_size=1, temperature=0.0)
        return " ".join(s.text.strip() for s in segments)


class Onnx:
    def __init__(self, model: str, int8: bool) -> None:
        import onnx_asr

        self.name = f"onnx:{model}{'-int8' if int8 else ''}"
        self._model = onnx_asr.load_model(model, quantization="int8" if int8 else None)

    def transcribe(self, pcm: bytes) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return str(self._model.recognize(audio, sample_rate=RATE))


class Mlx:
    def __init__(self, model: str) -> None:
        import mlx_whisper

        self.name = f"mlx:{Path(model).name}"
        self._mlx, self._model = mlx_whisper, model

    def transcribe(self, pcm: bytes) -> str:
        path = write_tmp_wav(pcm)
        try:
            return self._mlx.transcribe(path, path_or_hf_repo=self._model, language="uz")["text"]
        finally:
            Path(path).unlink(missing_ok=True)


def build(args: argparse.Namespace):
    factories = {
        "gemini": lambda: Gemini(args.gemini_model),
        "fwhisper": lambda: FWhisper(args.fw_model),
        "onnx": lambda: Onnx(args.onnx_model, args.onnx_int8),
        "mlx": lambda: Mlx(args.mlx_model),
    }
    for key in args.engines:
        before, started = rss_mb(), time.monotonic()
        try:
            engine = factories[key]()
        except Exception as exc:  # noqa: BLE001 - a missing package or model is a result
            print(f"-- {key}: skipped ({type(exc).__name__}: {str(exc)[:120]})")
            continue
        yield engine, time.monotonic() - started, before


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path.home() / "voice-bench")
    parser.add_argument(
        "--engines",
        nargs="+",
        default=["gemini", "fwhisper"],
        choices=["gemini", "fwhisper", "onnx", "mlx"],
    )
    parser.add_argument("--gemini-model", default="gemini-3.1-flash-lite")
    parser.add_argument("--fw-model", default="large-v3-turbo")
    parser.add_argument("--onnx-model", default="gigaam-multilingual-ctc")
    parser.add_argument("--onnx-int8", action="store_true")
    parser.add_argument("--mlx-model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--limit", type=int, default=0, help="only the first N clips")
    parser.add_argument("--out", type=Path, default=Path("stt_bench.json"))
    args = parser.parse_args()

    sentences = json.loads(SENTENCES.read_text(encoding="utf-8"))
    clips = sorted(args.dir.glob("[0-9][0-9].wav"))[: args.limit or None]
    if not clips:
        raise SystemExit(f"no NN.wav files in {args.dir}; run record.py first")
    data = [(int(p.stem), load_wav(p)) for p in clips]
    results = []
    for engine, load_s, rss_before in build(args):
        engine.transcribe(data[0][1])  # warm-up, not counted
        rows, times = [], []
        for number, pcm in data:
            started = time.monotonic()
            try:
                hyp = engine.transcribe(pcm)
            except Exception as exc:  # noqa: BLE001
                hyp = f"<error {type(exc).__name__}>"
            elapsed = time.monotonic() - started
            ref = sentences[number - 1]
            times.append(elapsed)
            rows.append(
                {
                    "n": number,
                    "ref": ref,
                    "hyp": hyp,
                    "wer": wer(ref, hyp),
                    "cer": cer(ref, hyp),
                    "s": round(elapsed, 2),
                    "cyr": has_cyrillic(hyp),
                }
            )
        p95 = sorted(times)[max(0, round(0.95 * len(times)) - 1)]
        results.append(
            {
                "engine": engine.name,
                "clips": len(rows),
                "wer": round(statistics.mean(r["wer"] for r in rows), 3),
                "cer": round(statistics.mean(r["cer"] for r in rows), 3),
                "median_s": round(statistics.median(times), 2),
                "p95_s": round(p95, 2),
                "load_s": round(load_s, 1),
                "added_peak_mb": round(rss_mb() - rss_before),
                "cyrillic": sum(r["cyr"] for r in rows),
                "rows": rows,
            }
        )
    print(
        f"\n{'engine':44s} {'WER':>6} {'CER':>6} {'median':>7} {'p95':>6} {'load':>6} {'+MB':>6}  cyr"
    )
    for r in results:
        print(
            f"{r['engine']:44s} {r['wer']:6.1%} {r['cer']:6.1%} {r['median_s']:6.2f}s "
            f"{r['p95_s']:5.2f}s {r['load_s']:5.1f}s {r['added_peak_mb']:6d}  {r['cyrillic']}"
        )
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\nper-clip detail: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
