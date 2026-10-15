"""Splits text into windows an embedding model can actually read.

`all-MiniLM-L6-v2` reads about 256 word-pieces at a time -- roughly 200
English words -- and silently ignores everything after that. Handing it a
four-thousand-word document does not fail; it returns a confident score for
the first paragraph and tells you nothing about the rest. An attack sitting
at the bottom of a long email would never be seen.

So long text is cut into windows and each one is scored. That is the fix,
and it is also the cost: twenty windows is twenty times the work, and twenty
chances to raise a false alarm on ordinary prose. A long document would
become *more* likely to be wrongly flagged than a short one.

The cap is therefore three windows, about six hundred words. Past that the
text is not scored and the result says so, so the gap is counted in real
traffic rather than guessed at. Patterns still run over the whole text, so a
long document keeps complete pattern coverage and partial semantic coverage.

Measured on an ordinary laptop CPU:

    short prompt      16ms        one window     103ms
    three windows    265ms        model load     7.3s, once at startup

A window is not a short prompt. A one-line prompt is about twelve tokens and
a full window is about 260, and the work scales with the tokens -- which is
why the first estimate written for this, 25-30ms for three windows, was out
by a factor of ten.

The cap survives that correction because the cost is already proportional to
the input: short prompts fill one window and pay 16ms, and only text long
enough to fill three pays 265ms. That same text makes the provider slower
too, so the share stays roughly constant rather than landing on top of a
fixed cost.

Stated in windows rather than milliseconds on purpose: a millisecond ceiling
is machine-dependent, and a test asserting one would pass here and fail in
CI. This project has already paid for one flaky test.

What this misses is an injection buried in the middle of a long document.
That is the hardest case in the field, it is not one a larger budget would
have solved either, and it is measured rather than claimed -- see the
buried-injection cases in the evaluation set.
"""

from dataclasses import dataclass

#: Words per window. MiniLM reads about 256 word-pieces, and English runs
#: near 1.3 word-pieces per word, so 200 words fills a window without
#: overflowing it.
WORDS_PER_WINDOW = 200

#: Words repeated from the end of one window at the start of the next.
#: Without it, an attack split across a boundary is two halves of a sentence,
#: and neither half means anything on its own.
OVERLAP = 25

#: How many windows get scored. One number, in one place, so that the
#: evaluation can report the catch rate at several values and the choice
#: comes from data rather than from this comment.
DEFAULT_MAX_WINDOWS = 3


@dataclass(frozen=True)
class Windowed:
    """Text prepared for scoring, and an honest account of what was left."""

    windows: tuple[str, ...]

    words: int
    """How many words the whole text had."""

    words_read: int
    """How many of them are inside the windows above."""

    @property
    def truncated(self) -> bool:
        """True when the cap stopped us short of the end.

        Recorded on the event, so how often real traffic hits the cap is a
        number somebody can look up rather than an assumption.
        """
        return self.words_read < self.words

    @property
    def coverage(self) -> float:
        return self.words_read / self.words if self.words else 1.0


def split(text: str, *, max_windows: int = DEFAULT_MAX_WINDOWS) -> Windowed:
    """Cut text into overlapping windows, up to the cap.

    Short text -- which is nearly all text -- comes back as a single window
    and costs one embedding, so the common case pays nothing for any of this.
    """
    words = text.split()

    if not words:
        return Windowed(windows=(), words=0, words_read=0)

    if max_windows < 1:
        return Windowed(windows=(), words=len(words), words_read=0)

    if len(words) <= WORDS_PER_WINDOW:
        return Windowed(windows=(text,), words=len(words), words_read=len(words))

    # Each window starts OVERLAP words before the previous one ended, so a
    # sentence spanning a boundary appears whole in the second window.
    step = WORDS_PER_WINDOW - OVERLAP

    windows: list[str] = []
    furthest = 0
    for start in range(0, len(words), step):
        if len(windows) == max_windows:
            break
        chunk = words[start : start + WORDS_PER_WINDOW]
        windows.append(" ".join(chunk))
        furthest = max(furthest, start + len(chunk))

    return Windowed(windows=tuple(windows), words=len(words), words_read=furthest)
