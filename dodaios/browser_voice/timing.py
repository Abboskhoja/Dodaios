"""Per-turn latency marks for the call / Mini App voice path, and their summary.

Marks are ``time.monotonic()`` seconds. A turn's stages:

* ``speech_end``      the end-of-turn detector handed the utterance over
* ``stt_start/end``   transcription
* ``brain_start``     the brain request left
* ``brain_first``     first streamed text
* ``brain_end``       the reply is complete
* ``tts_first_audio`` the first synthesized audio left for the speaker side

The speaker's own first sound and the person's real speech end exist only on
the device, so the totals here are "up to the audio leaving the server".
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field

__all__ = ["STAGES", "TurnTimings", "summarize"]

#: (name, from-mark, to-mark) in the order a turn passes through them.
STAGES: tuple[tuple[str, str, str], ...] = (
    ("handoff", "speech_end", "stt_start"),
    ("stt", "stt_start", "stt_end"),
    ("brain_request", "stt_end", "brain_start"),
    ("brain_first_token", "brain_start", "brain_first"),
    ("brain_total", "brain_start", "brain_end"),
    ("tts_first_audio", "brain_first", "tts_first_audio"),
    ("total_to_first_audio", "speech_end", "tts_first_audio"),
)


@dataclass
class TurnTimings:
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, at: float | None = None) -> None:
        """Record a mark once; a later repeat of the same mark is ignored."""
        self.marks.setdefault(name, time.monotonic() if at is None else at)

    def ms(self, start: str, end: str) -> float | None:
        a, b = self.marks.get(start), self.marks.get(end)
        if a is None or b is None or b < a:
            return None
        return round((b - a) * 1000, 1)

    def stages(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, start, end in STAGES:
            value = self.ms(start, end)
            if value is not None:
                out[name] = value
        return out


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[rank]


def summarize(turns: list[TurnTimings]) -> dict[str, dict[str, float]]:
    """Median, p95 and count per stage over ``turns`` (stages a turn lacks are skipped)."""
    per_stage: dict[str, list[float]] = {}
    for turn in turns:
        for name, value in turn.stages().items():
            per_stage.setdefault(name, []).append(value)
    return {
        name: {
            "median_ms": round(statistics.median(values), 1),
            "p95_ms": round(_percentile(values, 95), 1),
            "n": len(values),
        }
        for name, values in per_stage.items()
    }
