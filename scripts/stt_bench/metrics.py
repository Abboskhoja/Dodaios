"""Word and character error rates for Uzbek transcripts, script- and punctuation-tolerant."""

from __future__ import annotations

import re

_APOSTROPHES = "ʻʼ’‘`´′"
_CYR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "x",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "'",
    "ь": "",
    "ы": "i",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "ў": "o'",
    "қ": "q",
    "ғ": "g'",
    "ҳ": "h",
}


def normalize(text: str) -> str:
    """Lower-case Latin text with one apostrophe, no punctuation, single spaces.

    Uzbek Cyrillic is transliterated so a model that answers in Cyrillic is
    still compared fairly (and the script is reported separately by the runner).
    """
    text = text.lower()
    for mark in _APOSTROPHES:
        text = text.replace(mark, "'")
    text = "".join(_CYR.get(ch, ch) for ch in text)
    text = re.sub(r"[^\w' ]+", " ", text, flags=re.UNICODE).replace("_", " ")
    text = re.sub(r"'(?=\W|$)|(?<=\W)'", "", text)
    return re.sub(r"\s+", " ", text).strip()


def has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яёўқғҳ]", text.lower()))


def _edit_distance(a: list[str] | str, b: list[str] | str) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    return _edit_distance(ref, hyp) / max(1, len(ref))


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    return _edit_distance(ref, hyp) / max(1, len(ref))
