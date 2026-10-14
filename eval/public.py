"""Fetches public prompt-injection datasets into a gitignored folder.

    python -m eval --download

Why this is a script and not committed data: these sets are thousands of
rows, and they are other people's work under other people's licences. Ours to
measure against, not ours to re-publish. What *is* committed is this file,
which says exactly where every row came from.

Why it exists at all: the hand-written attack set contains 41 attacks I
thought of. Thresholds tuned only against those would be tuned against my own
imagination. These are attacks real people actually sent, including thousands
scraped from the places jailbreaks are traded.

Three rules this script follows, each of which the eval would be dishonest
without:

* **Provenance is written down.** Every file has a manifest recording the
  dataset, licence, config, split, row counts and a checksum, so a number in
  the final report can be traced to the exact rows that produced it.
* **Nothing downloaded reaches the holdout.** Public rows go in the tune
  pool only. A public row identical to a holdout case would quietly turn the
  one honest number in the phase into a tuned one.
* **Anything already in a committed file is skipped**, so the same attack
  cannot be counted twice -- once as hand-written and once as public.

The labels are other people's judgement calls and some of them are wrong.
The script reports where two sources disagree about the same text rather
than averaging the disagreement away.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from eval.dataset import (
    DOWNLOADED_DIR,
    load_all,
    load_attacks,
    load_benign,
    load_holdout,
    public_manifest,
)

#: Hugging Face serves dataset rows as JSON here, so nothing has to parse
#: parquet and the project gains no new dependency for a script most people
#: will never run.
ROWS_API = "https://datasets-server.huggingface.co/rows"

#: The API returns at most 100 rows per request.
PAGE = 100

MANIFEST = DOWNLOADED_DIR / "manifest.json"


@dataclass(frozen=True)
class Source:
    """One public dataset, and how to read it.

    Every set labels its rows differently -- `label: 0/1`, `type: benign`,
    `jailbreak: true` -- so each one brings its own reader, the same shape as
    the provider adapters in the gateway. A new dataset is a new entry in the
    list below, not a change to the fetching code.
    """

    name: str
    dataset: str
    licence: str
    config: str
    split: str
    text_field: str

    #: True for an attack, False for benign, None to skip the row.
    verdict: Callable[[dict], bool | None]

    #: Rows to take. The regular prompts from the in-the-wild set run to
    #: 13,735, which would be most of a megabyte of someone else's data for
    #: no extra signal.
    limit: int = 2000

    @property
    def path(self) -> Path:
        return DOWNLOADED_DIR / f"{self.name}.jsonl"


SOURCES = [
    Source(
        name="deepset-prompt-injections",
        dataset="deepset/prompt-injections",
        licence="apache-2.0",
        config="default",
        split="train",
        text_field="text",
        verdict=lambda row: bool(row["label"]),
    ),
    Source(
        name="deepset-prompt-injections-test",
        dataset="deepset/prompt-injections",
        licence="apache-2.0",
        config="default",
        split="test",
        text_field="text",
        verdict=lambda row: bool(row["label"]),
    ),
    Source(
        name="jailbreak-classification",
        dataset="jackhhao/jailbreak-classification",
        licence="apache-2.0",
        config="default",
        split="train",
        text_field="prompt",
        verdict=lambda row: {"jailbreak": True, "benign": False}.get(row["type"]),
    ),
    Source(
        name="in-the-wild-jailbreaks",
        dataset="TrustAIRLab/in-the-wild-jailbreak-prompts",
        licence="mit",
        config="jailbreak_2023_12_25",
        split="train",
        text_field="prompt",
        verdict=lambda row: bool(row["jailbreak"]),
    ),
    Source(
        name="in-the-wild-regular",
        dataset="TrustAIRLab/in-the-wild-jailbreak-prompts",
        licence="mit",
        config="regular_2023_12_25",
        split="train",
        text_field="prompt",
        verdict=lambda row: bool(row["jailbreak"]),
    ),
]


def normalise(text: str) -> str:
    """Collapse whitespace so the same prompt written with different line
    breaks is recognised as the same prompt."""
    return " ".join(text.split()).strip().lower()


def committed_texts() -> set[str]:
    """Everything already in a file under version control, including the
    holdout.

    The holdout is in here for the reason the whole phase depends on: if a
    public row is the same prompt as a holdout case, and it lands in the tune
    pool, then the threshold gets fitted to a case that is supposed to be
    grading it. The holdout would still look untouched. It would not be.
    """
    committed = [*load_attacks(), *load_benign(), *load_holdout(), *load_all()]
    return {normalise(case.text) for case in committed}


#: Tries per page before giving up on a source.
TRIES = 6

#: A pause between pages. Fifty-odd requests in a burst trips the limiter
#: and then everything afterwards waits, so the polite version is faster
#: than the greedy one. This is somebody else's free service.
BETWEEN_PAGES = 0.5


class RateLimited(Exception):
    """The API asked us to slow down and we ran out of patience."""


def get_page(client: httpx.Client, params: dict) -> dict:
    """One page, retrying when the server asks us to wait.

    This is a free public service being asked for five datasets in a row, so
    a 429 is the expected answer rather than an exceptional one. The first
    version of this script had no retry, and the failure it produced was
    thoroughly misleading: the *fifth* dataset reported an error, because the
    first four had used up the request budget. Nothing was wrong with the
    fifth dataset at all.
    """
    delay = 2.0

    for attempt in range(1, TRIES + 1):
        response = client.get(ROWS_API, params=params, timeout=60)

        if response.status_code != 429:
            response.raise_for_status()
            return response.json()

        if attempt == TRIES:
            break

        # Honour Retry-After when the server sends one, because guessing
        # against a rate limiter is how you stay rate limited.
        wait = float(response.headers.get("retry-after") or delay)
        print(
            f"\n    rate limited, waiting {wait:.0f}s (attempt {attempt} of {TRIES}) ... ",
            end="",
            flush=True,
        )
        time.sleep(wait)
        delay = min(delay * 2, 60)

    raise RateLimited(f"still rate limited after {TRIES} attempts")


def fetch_rows(source: Source, *, client: httpx.Client) -> tuple[list[dict], int]:
    """Page through one dataset. Returns the raw rows and the total the API
    says exist, so the manifest can record what was left behind."""
    rows: list[dict] = []
    total = 0

    while len(rows) < source.limit:
        body = get_page(
            client,
            {
                "dataset": source.dataset,
                "config": source.config,
                "split": source.split,
                "offset": len(rows),
                "length": min(PAGE, source.limit - len(rows)),
            },
        )

        total = body.get("num_rows_total", total)
        page = [item["row"] for item in body.get("rows", [])]
        if not page:
            break

        rows.extend(page)
        if len(rows) < source.limit and len(rows) < total:
            time.sleep(BETWEEN_PAGES)

    return rows[: source.limit], total


def convert(
    source: Source, rows: list[dict], *, skip: set[str]
) -> tuple[list[dict], dict[str, int]]:
    """Turn one dataset's rows into our own shape, dropping what we cannot
    use. The counts come back so the report can say what was dropped and why
    -- a silent drop is how a dataset half-loads and nobody notices."""
    cases: list[dict] = []
    seen: set[str] = set()
    counts = {"kept": 0, "no_label": 0, "empty": 0, "duplicate": 0, "already_ours": 0}

    for row in rows:
        text = (row.get(source.text_field) or "").strip()
        if not text:
            counts["empty"] += 1
            continue

        verdict = source.verdict(row)
        if verdict is None:
            counts["no_label"] += 1
            continue

        key = normalise(text)
        if key in skip:
            counts["already_ours"] += 1
            continue
        if key in seen:
            counts["duplicate"] += 1
            continue

        seen.add(key)
        counts["kept"] += 1
        cases.append({"text": text, "attack": verdict, "source": source.name})

    return cases, counts


def as_line(case: dict) -> str:
    """One JSON object on one line, for real.

    `ensure_ascii=False` keeps the German, Spanish and Hindi prompts readable
    instead of turning them into \\u escapes, and it also leaves U+2028 LINE
    SEPARATOR exactly as it found it -- which JSON considers an ordinary
    character and `str.splitlines()` considers a line break. Five prompts in
    the in-the-wild sets contain one, and they split a file of 1,032 records
    into 1,036 lines, four of which were fragments that would not parse.

    So the two characters that disagree are escaped by hand. Nothing else
    needs it: `json.dumps` already escapes the newline, the carriage return
    and the quote.
    """
    line = json.dumps(case, ensure_ascii=False)
    return line.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write(source: Source, cases: list[dict]) -> str:
    """Write one file and return its checksum."""
    body = "\n".join(as_line(case) for case in cases) + "\n"
    source.path.write_text(body, encoding="utf-8", newline="\n")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def label_conflicts(cases: list[dict]) -> list[str]:
    """Prompts that one source calls an attack and another calls benign.

    Not a bug to be fixed -- it is a measurement. Public labels are other
    people's judgement calls, and knowing how often they disagree is the
    difference between a score and a score you can defend.
    """
    verdicts: dict[str, set[bool]] = {}
    for case in cases:
        verdicts.setdefault(normalise(case["text"]), set()).add(case["attack"])
    return [text for text, found in verdicts.items() if len(found) > 1]


def read_existing(source: Source) -> list[dict]:
    # split("\n"), never splitlines() -- see as_line() above for the five
    # characters that make the difference.
    lines = source.path.read_text(encoding="utf-8").split("\n")
    return [json.loads(line) for line in lines if line.strip()]


def download(*, refresh: bool = False) -> None:
    DOWNLOADED_DIR.mkdir(parents=True, exist_ok=True)
    skip = committed_texts()

    #: What a previous run already fetched. Kept rather than re-requested,
    #: because the way this script fails is by asking for too much too
    #: quickly, and the fix for that is to ask for less.
    previous = {entry["name"]: entry for entry in (public_manifest() or [])}

    print(f"Fetching {len(SOURCES)} public datasets into {DOWNLOADED_DIR}")
    print(f"Skipping anything already in a committed file ({len(skip)} cases)\n")

    manifest = []
    everything: list[dict] = []

    with httpx.Client(follow_redirects=True) as client:
        for source in SOURCES:
            print(f"  {source.name} ... ", end="", flush=True)

            if not refresh and source.path.is_file() and source.name in previous:
                kept = read_existing(source)
                everything.extend(kept)
                manifest.append(previous[source.name])
                print(f"{len(kept):>5} already downloaded, skipped (--refresh to redo)")
                continue
            try:
                rows, total = fetch_rows(source, client=client)
            except RateLimited as exc:
                print(f"FAILED -- {exc}. Wait a few minutes and run it again;")
                print("         files already written are kept and will be skipped.")
                continue
            except httpx.HTTPStatusError as exc:
                # The status code is the whole message. "HTTPStatusError" told
                # the first run nothing, and the cause turned out to be a 429
                # from the requests before it.
                print(f"FAILED -- HTTP {exc.response.status_code} from {source.dataset}")
                continue
            except httpx.HTTPError as exc:
                print(f"FAILED -- {type(exc).__name__}: {exc}")
                continue

            cases, counts = convert(source, rows, skip=skip)
            checksum = write(source, cases)
            everything.extend(cases)

            attacks = sum(1 for case in cases if case["attack"])
            print(
                f"{counts['kept']:>5} kept  ({attacks} attacks, "
                f"{counts['kept'] - attacks} benign)  of {total} available"
            )

            manifest.append(
                {
                    "name": source.name,
                    "dataset": source.dataset,
                    "licence": source.licence,
                    "config": source.config,
                    "split": source.split,
                    "rows_available": total,
                    "rows_fetched": len(rows),
                    "dropped": {k: v for k, v in counts.items() if k != "kept" and v},
                    "cases": counts["kept"],
                    "attacks": attacks,
                    "sha256": checksum,
                    "fetched_at": datetime.now(UTC).date().isoformat(),
                }
            )

    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    attacks = sum(1 for case in everything if case["attack"])
    conflicts = label_conflicts(everything)

    print(f"\n{len(everything)} cases: {attacks} attacks, {len(everything) - attacks} benign")
    print(f"Licences: {', '.join(sorted({entry['licence'] for entry in manifest}))}")
    print(f"Manifest: {MANIFEST}")

    if conflicts:
        print(f"\n{len(conflicts)} prompts are labelled both ways by different sources.")
        print("Not a bug -- public labels are judgement calls. Worth reporting in the gate:")
        for text in conflicts[:3]:
            print(f"  {text[:70]!r}")
