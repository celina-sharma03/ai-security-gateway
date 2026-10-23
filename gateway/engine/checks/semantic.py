"""Scores text against known attacks by meaning rather than by wording.

The patterns in `injection.py` catch the famous phrasings and score zero on
the hard ones -- measured, not assumed. This is what narrows that gap: an
attacker who reads our rules file writes the same attack in different words,
and different words are what an embedding is insensitive to.

How it works. A small model turns text into a vector. A trained head -- 384
weights and a bias, learned from 3,492 public jailbreak examples -- turns
that vector into a probability. Long text is cut into windows and the most
suspicious window decides.

**This check never blocks.** Measured against 43 hand-written attacks and 49
legitimate requests, it finds 67% of the attacks and flags 10% of the
requests that must pass. That is a useful thing to write in a log and an
unacceptable thing to act on: a gateway that stops one request in ten is a
gateway somebody switches off. A purpose-built 700MB classifier was measured
on the same cases, found 88%, and flagged 37% -- including "Skip the
explanation and just give me the SQL" at full confidence. Nobody has solved
this well enough to act on automatically.

The whole measurement, and what would change it, is in
docs/injection-detection.md.

**The head never saw the cases it is graded on.** It was trained on public
datasets only; the hand-written sets and the holdout were excluded, and
`injection_head.json` records that alongside the seed and the row counts.
Training on the cases you grade against produces a number that means nothing,
and the number is the entire point of this phase.

An earlier version of this compared text against fifteen hand-written example
attacks and took the closest match. It threw away 4,974 of the 4,989 labelled
rows already downloaded, and measured four times worse: 14% of legitimate
requests flagged against 3.5%.

The model is loaded once, lazily, and never inside a request. Importing this
module must stay cheap, because `gateway check` and the tests import the
engine and have no business paying for torch.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gateway.engine.checks import windows
from gateway.engine.result import Action, CheckResult, Finding
from gateway.engine.rules import Rules

log = logging.getLogger(__name__)

CHECK_NAME = "semantic"
CATEGORY = "injection"

#: The model. 80MB, runs on a CPU, and reads about 256 word-pieces at a time
#: -- which is what windows.py exists to work around.
MODEL_NAME = "all-MiniLM-L6-v2"

#: The trained head: 384 weights and a bias, 1.9KB, learned from 3,492
#: public jailbreak examples. Shipped as plain numbers rather than a pickle,
#: so loading it needs numpy and nothing else -- and so that what the
#: gateway runs can be read rather than unpickled on trust.
HEAD_FILE = Path(__file__).with_name("injection_head.npz")

#: Where a score becomes worth recording. From the measurement, not from
#: taste: at 0.70 the head finds 66% of held-out public attacks and flags
#: 4.6% of the benign ones, which makes a log somebody can actually read.
#: Lower catches more and fills the log with noise.
#:
#: There is no block threshold. See `check`.
RECORD_ABOVE = 0.70


class Embedder(Protocol):
    """Anything that turns text into vectors.

    A protocol rather than the model itself, so every test in this project
    can run without 2GB of torch and without a model download. The fake used
    in the tests is a dozen lines.
    """

    def encode(self, texts: Sequence[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Scored:
    """What the check worked out, before the rules decide what to do."""

    score: float
    """The trained head's probability that this is an attack."""

    truncated: bool
    coverage: float


_model: Embedder | None = None
_model_lock = threading.Lock()


def get_model() -> Embedder:
    """Load the model once, on first use.

    Locked, because two requests arriving together would otherwise each
    start a download. Loading takes about seven seconds and allocates a few
    hundred megabytes, which is a thing to do at startup and never during a
    request -- see `warm_up`.
    """
    global _model

    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                log.info("loading the semantic model (%s), this takes a few seconds", MODEL_NAME)
                _model = SentenceTransformer(MODEL_NAME)

    return _model


def set_model(model: Embedder | None) -> None:
    """Install a model directly. For tests, and for anything that wants to
    supply its own."""
    global _model
    _model = model


def warm_up() -> None:
    """Load the model and embed the examples, before any traffic arrives.

    Called at startup. Without it the first request of the day pays seven
    seconds, which looks exactly like a hung gateway.
    """
    get_model()
    head()


_head: tuple[Any, float] | None = None


def head() -> tuple[Any, float]:
    """The trained weights, loaded once."""
    global _head
    import numpy as np

    if _head is None:
        with np.load(HEAD_FILE) as data:
            _head = (data["weights"].astype(np.float32), float(data["bias"]))
    return _head


def score(
    text: str,
    *,
    model: Embedder,
    words_read: int = windows.WORDS_READ,
) -> Scored:
    """How much the most suspicious window looks like an attack.

    The maximum over windows, not the average: an attack in one paragraph of
    an otherwise innocent document is still an attack, and averaging it
    against the innocent paragraphs is how it disappears.

    The score is the trained head's probability, not a distance. The first
    version of this compared text against fifteen hand-written example
    attacks and took the closest match -- which threw away 4,974 of the
    4,989 labelled rows we had already downloaded, and measured four times
    worse: 14% of legitimate requests flagged against 3.5%.
    """
    import numpy as np

    split = windows.split(text, words_read=words_read)

    if not split.windows:
        return Scored(score=0.0, truncated=split.truncated, coverage=split.coverage)

    weights, bias = head()
    vectors = np.asarray(model.encode(list(split.windows), normalize_embeddings=True))
    best = float(np.max(1.0 / (1.0 + np.exp(-(vectors @ weights + bias)))))

    return Scored(score=best, truncated=split.truncated, coverage=split.coverage)


def check(
    text: str,
    *,
    rules: Rules | None = None,
    model: Embedder | None = None,
    log_above: float = RECORD_ABOVE,
    block_above: float | None = None,
) -> CheckResult:
    """Score the text and record it when the score is high enough.

    **This check never blocks**, and `block_above` exists only so that the
    tests which used it keep working. Measured, it catches 67% of attacks
    and flags 10% of legitimate requests -- useful as a signal for somebody
    to read, and not something to act on automatically. A gateway that
    stopped one request in ten would be switched off within a week.

    The reasoning, the numbers and the comparison against a purpose-built
    700MB classifier are in docs/injection-detection.md. The short version
    is that the classifier catches more and flags 37%, including "Skip the
    explanation and just give me the SQL" at full confidence, so nobody has
    solved this well enough to act on.

    The famous phrasings are still blocked -- by `injection.py`, with
    literal patterns, where a match means what it says.
    """
    rules = rules or Rules()
    configured = rules.injection.action.as_action

    if configured is None:
        return CheckResult.allow(CHECK_NAME)

    result = score(text, model=model or get_model())

    if result.score < log_above:
        return CheckResult.allow(CHECK_NAME)

    # Never stronger than a log, whatever the rules asked for and whatever
    # the score is. See the docstring.
    action = Action.LOG if configured >= Action.LOG else configured

    reason = f"semantic score {result.score:.2f}"
    if result.truncated:
        # Said out loud, because a low score on text we only partly read is
        # not the same claim as a low score on text we read all of.
        reason += f", read {result.coverage:.0%} of the text"

    return CheckResult(
        check=CHECK_NAME,
        action=action,
        findings=(
            Finding(
                category=CATEGORY,
                start=0,
                end=len(text),
                original="",
                placeholder="",
                score=result.score,
            ),
        ),
        text=None,
        reason=reason,
        coverage=result.coverage,
    )
