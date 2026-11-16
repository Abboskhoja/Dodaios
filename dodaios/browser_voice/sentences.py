"""Cut a streaming reply into speakable sentences as soon as each one is whole.

The brain streams text; text-to-speech wants whole sentences. Cutting at the
first sentence end lets speech start while the rest is still being written, so
the first audible word no longer waits for the whole reply.
"""

from __future__ import annotations

import re

__all__ = ["next_sentence_break"]

#: A sentence end: ``. ! ? …`` (optionally followed by closing quotes or
#: brackets) and then whitespace. A dot inside a number ("3.5") has no
#: whitespace after it and is never a break.
_END = re.compile(r"[.!?…]+[\"'”’»)\]]*(?=\s)|\n+")

#: The first sentence may be short, so speech starts early; later ones are
#: joined until they are long enough to sound like a phrase, not a stutter.
FIRST_MIN_CHARS = 12
NEXT_MIN_CHARS = 28


def next_sentence_break(buffer: str, *, first: bool) -> int | None:
    """The index just past the first complete sentence in ``buffer``, or ``None``.

    ``first`` selects the shorter minimum used for the very first sentence.
    """
    minimum = FIRST_MIN_CHARS if first else NEXT_MIN_CHARS
    for match in _END.finditer(buffer):
        end = match.end()
        if len(buffer[:end].strip()) >= minimum:
            return end
    return None
