"""Uzbek-aware WER/CER used by the STT benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "stt_bench"))

from metrics import cer, has_cyrillic, normalize, wer  # noqa: E402


def test_punctuation_case_and_apostrophe_variants_do_not_count():
    assert normalize("O‘zbek tilida, Python!") == "o'zbek tilida python"
    assert wer("Bugun nimalar qilamiz?", "bugun nimalar qilamiz") == 0.0


def test_cyrillic_answers_are_compared_in_latin():
    assert has_cyrillic("Салом, Жарвис")
    assert wer("salom jarvis", "Салом Жарвис") == 0.0


def test_errors_are_counted_per_word_and_per_character():
    assert wer("bir ikki uch", "bir uch") == 1 / 3
    assert 0 < cer("salom", "salam") < 0.3
    assert wer("bir", "") == 1.0
