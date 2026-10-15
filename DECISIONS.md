# AI Security Gateway — Decisions

**Status:** Discussion complete. Ready to build.
**Last updated:** 2026-09-11

---

## What this is

A self-hosted proxy that sits between **anyone using an LLM** and the provider they're calling.

Before a prompt leaves the building, the gateway redacts personal data and secrets, and detects attempts to manipulate the AI. Everything that happens is recorded, attributed to whoever sent it, and visible in a dashboard.

It is not tied to one kind of user and not tied to one provider. A company protecting its staff, a startup protecting its app, a solo developer, a research team — the gateway doesn't know or care which.

**The one-line answer:** *"It's a self-hosted proxy. You run it on your own infrastructure. I host nothing, and none of your data ever reaches me."*

Three words: **Self-hosted. Local. Nothing leaves.**

### Build generic, describe specific

The code is generic. The **documentation** tells one clear story, because "it's for everyone using LLMs" convinces nobody — a reader needs to recognise themselves in the first sentence. The lead story is *"stop your team pasting customer data into ChatGPT."* Anyone else can still use it exactly as-is.

### The flow

```
╔══════════════════════════════════════════════════╗
║   THEIR INFRASTRUCTURE — machines they control   ║
║                                                  ║
║    a person or an app                            ║
║              │                                   ║
║              ▼                                   ║
║        THE GATEWAY                               ║
║        checks + model file, on disk              ║
║        ✱ no internet used here ✱                 ║
║              │                                   ║
╚══════════════╪═══════════════════════════════════╝
               │  ← the only thing crossing out
               ▼
      OpenAI · Anthropic · Google · Azure · Ollama · …
```

Blocked requests never cross the wall at all.

### Questions people will ask

**"Is it offline or online?"** It's a running server, but it runs inside their own network. Detection is fully offline — the model runs locally. The only thing that goes out is the request to the provider, which was already going there anyway.

**"Hosted platform or offline library?"** Neither. It's a program they deploy on their own servers. Not hosted by us, not a library inside their code.

**"Does it live in their codebase?"** No. It runs as its own service. They change one line — the API address — to point at their own copy.

**"Why don't you host it?"** A security tool that requires handing over all your sensitive prompts defeats its own purpose. Hosting would also make us a data processor under GDPR and DPDP.

---

## Settled — the product

**Full proxy.** Traffic flows through the gateway. Switching means changing one line — the `base_url` — from the provider's address to the gateway's. Consequence: the gateway must imitate the provider's API format exactly, or that one-line change breaks.

**Self-hosted.** They run it. We host nothing, hold no keys, see no prompts, pay for no servers. Our compliance burden is close to zero because we never touch the data.

**Any provider.** Never hardcoded to OpenAI. The gateway accepts the OpenAI-compatible format because it's the de-facto standard — which already covers Azure, Together, Groq, OpenRouter, Ollama and vLLM — and a provider adapter layer routes outbound to anything, including Anthropic and Google. The upstream address is always configuration, never code. Adding a provider means adding one file.

**Per-key identity.** Whoever holds a key is a principal. An employee, a service, an app, a script — the gateway records which key sent what and doesn't care what kind of thing it is.

**Multi-tenant from the first migration.** Every record carries a tenant ID. This is labels only — no tenant admin screens, no per-tenant billing, no isolation guarantees yet. Costs nothing now; retrofitting it means rewriting every query in the application.

**Operated by whoever runs it.** They deploy it, watch the dashboard, and tune the rules.

**Cost tracking per key.** Token counts and estimated spend are recorded on every event, attributed to the key that sent it.

This is nearly free — the gateway is already reading every request and every response, so the numbers are sitting in its hands. But it answers a second, unrelated problem people genuinely have: when a provider bill jumps from $200 to $1,800, nobody can explain why, because the bill is a single number. Here the answer is one dashboard query — a service key with a broken retry loop made 40,000 calls.

Built in V2, but the token columns go into the schema from the first migration.

---

## Settled — detection

**Named categories, not one flat list.** `rules.yaml` holds multiple categories — `pii`, `injection`, `malware`, and anything added later — each with its own examples and its own thresholds. Cheap to design in now, awkward to retrofit onto a flat list.

**Order of work: PII and secrets first, prompt injection second.** Redaction is pattern matching — reliable from day one, and it's the pain people actually have. Injection detection is the hard, uncertain part, built on a foundation that already works. If it disappoints, there's still a real product.

**Redaction starts with rigid patterns** — API keys, credit cards, emails, phone numbers, Aadhaar, PAN, UPI IDs. These have distinctive shapes and are caught almost perfectly. Names and addresses look like ordinary words ("Rose", "Reading") and are a genuine research problem.

**Reference numbers are not redacted.** Transaction IDs, RRNs and UPI payment references identify a transaction, not a person — none of them can move money or reveal who someone is without the bank's own systems. And *"my payment failed, here's the reference"* is one of the most common support questions; the AI needs the number to help. Redacting it breaks the question and protects nothing.

**Masked values are not redacted.** When someone has already hidden the digits themselves — `78966*****@ibl`, `789678****` — what remains can't identify anyone. Redacting it would also strip clues the answer depends on: `@ibl` tells the AI the app is PhonePe.

**A UPI ID hides the name and keeps the handle.** `Celina@AXIS` becomes `[UPI_NAME_1]@AXIS`. The name is personal; the handle only says which bank or app, and it's usually what the answer needs — the AI can only reply "@axis isn't a real handle, did you mean @axisbank?" if it can see @axis.

**A UPI ID is recognised without the word "upi" when any one of three signs is present:** the handle is one a UPI app issues (`ravi@ybl`), the name is a 10-digit mobile number (`9123456780@newbank`), or the handle names a bank (`Celina@AXIS`). Server logins like `admin@localhost` show none of them. The accepted gap is something like `celina@newbank` with no "upi" nearby, which is genuinely indistinguishable from `raj@home`.

**One engine serves every semantic category.** Prompt injection and malware-rephrase detection are the same machinery — take incoming text, compare it semantically against known-bad examples. Same scoring, same thresholds. Only the examples in the config differ. Building injection detection *is* building the rephrase detector; adding malware afterwards is adding a category to a YAML file, not writing code.

**Injection detection is patterns plus embeddings, measured against an off-the-shelf classifier.** Patterns catch the phrasings that are literally famous — *"ignore all previous instructions"* — and cost microseconds. Embeddings catch the paraphrases, which is the whole reason patterns are not enough: an attacker who reads our rules file writes the same attack in different words. `all-MiniLM-L6-v2` is about 80MB and runs on a CPU, so the gateway stays something you can run on a small box, and it fits the engine already decided above — examples in YAML, scored by similarity.

A purpose-built classifier is the measuring stick, not the plan. Those models are around 700MB and exist for exactly this job, so if one beats our approach by a wide margin that is a fact worth publishing rather than hiding. The comparison is in the exit criteria this time, not in the prose, because that is how the Presidio comparison got missed twice.

**Three data sources, and only one of them is committed.** Hand-written attacks grouped by family — instruction override, role-play, encoding, context-stuffing, indirect injection — live in the repository, because they are what the thresholds get tuned against and a reader has to be able to see them. Public jailbreak datasets are fetched by a script into a gitignored folder, because they are large and other people's licences are not ours to re-publish. And the holdout cases are written separately, afterwards, by hand.

The negative set matters more here than it did for PII. *"How do I stop prompt injection in my own app?"* is a security question, not an attack; so is a novelist asking for a villain's dialogue. A detector that blocks those is worse than no detector, because the people it annoys are the people who chose to install it.

**The semantic check reads the first three windows and no further.** MiniLM reads about 256 word-pieces at a time, so a long prompt has to be cut into windows and scored one at a time. The cap is three — roughly the first 600 words — and patterns still run over the whole text, so a long document keeps complete pattern coverage and partial semantic coverage.

**Measured, after this was first written with a guess in it.** The guess said three windows would cost 25-30ms. It costs about 265ms, which is ten times out, because a window is not a short prompt: a one-line prompt is around twelve tokens and a full window is around 260, and the work scales with the tokens.

    short prompt           16ms      one window      103ms
    three windows         265ms      model load      7.3s, once at startup

The cap survives the correction, because the cost is already proportional to the input. Short prompts fill one window and pay 16ms, which is the 3% of a 500ms model call that the original budget asked for. Only text long enough to fill three windows pays 265ms — and that same text makes the provider slower too, since prefill scales with input, so the share stays near 15% rather than landing on top of a fixed cost.

Dropping to one window would save that quarter-second in exactly the place the risk is: real jailbreaks have a median length of 236 words, so more than half of them would be read only in part. That is the wrong way round.

ONNX with int8 quantisation was measured as a way to buy the tail back and was **rejected: it is slower**, 0.6-0.8x on windows, presumably because torch uses Intel's MKL on this hardware and the ONNX session does not thread as well. Written down so nobody tries it twice.

The cap is stated in windows rather than milliseconds on purpose. A millisecond ceiling is machine-dependent, so a test asserting it would pass here and fail in CI, and this project has already paid for one flaky test.

Scanning further would cost more than time. Every extra window is another chance to raise a false alarm on ordinary prose, so a twenty-window document would get twenty rolls of the dice and become *more* likely to be wrongly flagged than a short one. A bigger budget would have widened the window a little while making precision worse.

What it will miss is an injection buried in the middle of a long document, which is the hardest case in this field and not one a 200ms budget would have solved either. So the gap is handled by admitting it rather than by pretending: buried-injection cases go in the eval so the report states a measured miss rate, the event records when the check stopped early so real traffic can be counted rather than guessed at, and the limit is written in `docs/what-it-protects.md` beside the others. The number lives in `rules.yaml`, so a deployment that would rather pay the latency can raise it without touching code.

**A pipeline, not a single check.** Every check shares one shape, so adding check two or three doesn't force a redesign.

**Four actions:** allow · log-but-allow · redact · block.
Every check returns an action, a reason or score, and — when it changed the text — the replacement **plus a mapping back to the original**. A bare true/false won't work, because redaction needs somewhere to put the cleaned text. And text alone won't work either, because of reversible redaction below.

**Shadow mode.** A global setting that turns every `block` and `redact` into a `log`. The gateway checks everything and records what it *would* have done, while traffic passes through untouched.

This is the single biggest adoption unlock in the project. Nobody wants to put an untested filter in front of production traffic — the first question anyone asks is *"will this break my app?"* Shadow mode answers it with evidence: run a week, read the log, fix the false alarms, then switch to enforce. Without it, the first install is a gamble most people won't take.

Costs almost nothing to build, but it has to be designed in — it's a mode that overrides what each check decided, applied in one place.

**Reversible redaction.** Redacted values are replaced with numbered placeholders, and the mapping is held for the lifetime of the request so the real values can be restored in the response.

```
person asks:     "Write a follow-up to priya.sharma@acme.com about invoice 4471"
provider sees:   "Write a follow-up to [EMAIL_1] about invoice 4471"
provider replies: "... addressed to [EMAIL_1] ..."
person receives: "... addressed to priya.sharma@acme.com ..."
```

The provider never saw the real address. The person never noticed anything happened.

Plain blanking breaks the answer — you get a draft email addressed to `[EMAIL]`, which is useless and annoying, and annoying security tools get uninstalled. This is the difference between something people tolerate and something they never notice.

Built in V2, but the result shape must carry the mapping from day one or it can't be added later.

Which action fires depends on what was found. If the problem is *part* of the message, redact that part. If the problem is *the whole point* of the message, block it. If unsure, log and allow — nobody gets punished on a maybe.

**Two thresholds, three zones** for score-based checks:

```
score  0.0 ────── low ────── high ────── 1.0
           allow       log &      block
           silently    allow
```

**Threshold values come from the data, not a guess.** Any number written down before the real model has run over real data is a placeholder.

**Redaction uses no thresholds.** A pattern either matches or it doesn't. The two check types behave differently by nature, which the shared action shape already handles.

**Rules live in a plain editable config file**, shipped with working defaults. It's *config, not code* — editing changes what the gateway does about what it finds, never how finding works. And the person editing is the operator, not the person being checked.

**The file controls detection; it doesn't contain it.** For each category the operator chooses `redact`, `block`, `log` or `off`, and can add patterns of their own — employee IDs, project codenames. The built-in patterns, checksums and context rules stay in code, where they're tested, so an edit can't quietly break them. (The alternative, moving every regex into the file, would expose the fragile part to editing, and still couldn't hold logic like Luhn.)

**A mistake in the file is an error, never a silent fallback.** An unknown category, an unknown action, or a broken regex stops the gateway starting, with a message saying what's wrong. An operator who writes `phnoe: off` believes phone numbers are no longer redacted; carrying on quietly would leave them wrong about their own gateway. A category left out of the file is redacted, so deleting a line can never switch detection off.

---

## Settled — architecture

**Engine separate from plumbing.** Detection logic is its own importable piece with no knowledge of web requests. The proxy is a thin layer that unwraps a request, asks the engine, and passes the answer along. Detection can be tested with no server running.

**The dashboard is the full-stack frontend.** Not an extra. A proxy is invisible by nature — there's no product interface to build, because nobody clicks on a proxy. The operator-facing dashboard is the *only* frontend this project can have, which means without it "full stack" isn't true. It includes login and accounts, the live log of what got caught and by whom, and settings for managing rules.

**Everything deploys as one program.** The React frontend builds to static files served by FastAPI. No second server, no Node in production. "One command and it's running" is the difference between someone trying it and someone leaving.

**Fail open.** If a check crashes, or the model fails to load, traffic passes through unchecked rather than the gateway going down. A security tool that takes the product offline gets uninstalled. Every fail-open event is logged loudly so it can't pass unnoticed. Revisit this before anyone runs genuinely sensitive traffic through it in production.

**A blocked request returns a normal-shaped response, not an HTTP error.** If the gateway returns a 403, the provider's SDK raises an exception and the calling application breaks — which makes the gateway look like the problem. Instead it returns a well-formed completion whose content explains that the request was blocked and why. The app keeps working, the person sees a clear message, and nothing has to be special-cased on the client side.

---

# Tech Stack

The complete list. Nothing is cut — things get added as they're genuinely needed, phase by phase.

## Language
**Python 3.12** — the entire embedding and NLP ecosystem is Python.

## API and proxy layer
**FastAPI** — the web framework. Async, so it holds many requests open while waiting on providers.
**uvicorn** — the server that runs it.
**Pydantic** — validates every request and response shape.
**httpx** — outbound calls to providers, with proper streaming.

## Provider layer
**A custom adapter per provider** — OpenAI, Anthropic, Google, Azure, Ollama, and anything OpenAI-compatible.

## Detection layer
**`re`** (standard library) — rigid patterns: cards, emails, phones, API keys, Aadhaar, PAN.
**Microsoft Presidio** — richer PII detection including names. Measured after Phase 3 rather than assumed better, and **not adopted for V1**: on our categories it scores 20/61 against 61/61, has no recognizer at all for API keys, passwords or UPI IDs, and out of the box flags `invoice 9876543210 is still unpaid` as a UK NHS number at confidence 1.0. Full numbers and reasoning in `docs/presidio-comparison.md`. Revisit in V2 for **names only**, behind a rules-file switch and with the irrelevant recognizers disabled — names are the one thing patterns genuinely cannot do.
**sentence-transformers** with **all-MiniLM-L6-v2** — semantic detection. Runs locally after a one-time ~80MB download.

## Data layer
**SQLAlchemy** — the ORM, so storage isn't welded to one database.
**SQLite** — the default. A single file, zero setup, keeps installation to one program.
**PostgreSQL** — supported alternative for larger deployments. A config change, not a rewrite.
**Alembic** — schema migrations.

## Config layer
**PyYAML** — `rules.yaml`: named categories, their examples, their thresholds.
**pydantic-settings** — environment variables and secrets, validated at startup.

## Auth layer
**API keys** — per-key identity for gateway traffic.
**JWT sessions** — dashboard login.
**passlib with bcrypt** — password hashing.

## Frontend
**React** · **Vite** · **TypeScript** · **Tailwind CSS** · **TanStack Query** · **Recharts**
Built to static files, served by FastAPI.

## Logging
**structlog** — structured, queryable logs. This is the audit trail, so it matters more than usual.

## Testing
**pytest** · **pytest-asyncio** · **httpx test client**

## Code quality
**ruff** — linting and formatting in one fast tool.
**mypy** — type checking.

## Packaging and deployment
**Docker** and **docker-compose** — one command to run it.
**requirements.txt** with pip.

---

## Folder structure

Structured from the start. Every folder maps to a decision already made.

```
ai-security-gateway/
├── gateway/
│   ├── engine/          ← the brain. no web code in here.
│   │   ├── checks/      ← one file per check
│   │   ├── pipeline.py  ← runs the checks in order
│   │   └── result.py    ← the shared action shape
│   ├── providers/       ← one adapter per LLM provider
│   ├── proxy/           ← the plumbing. thin.
│   │   └── app.py       ← FastAPI
│   ├── storage/         ← models, migrations, queries
│   ├── auth/            ← keys, sessions, tenants
│   └── config/
│       └── rules.yaml   ← the file operators can edit
├── eval/                ← test data + scoring harness
├── dashboard/           ← React frontend
├── tests/
├── docs/
├── docker-compose.yml
└── requirements.txt
```

---

## Phases

Each exits on something you can demonstrate, not a feeling of doneness.

### V1 — "it stops secrets leaking"

**0 — Setup.** Folder skeleton, virtual environment, dependencies, git, Docker.
*Exit: the project runs and prints something.*

**1 — Test data.** Synthetic PII and secrets — cards, keys, emails, phones, Aadhaar, PAN — plus realistic cases that must *not* be caught. Ordinary tests rather than a tune/holdout split: a regex either matches or it doesn't, so there is no threshold to tune. The set still has to keep growing with cases written *without looking at the patterns* — see the working practice below, which Phase 2 proved the hard way. Real PII can't be published, so this is generated rather than downloaded.
*Exit: a test suite that fails loudly when a pattern is wrong.*

**2 — Redaction engine.** Rigid patterns. Regex measured against Presidio — run late, after Phase 3, because the exit criteria below never mentioned it and nothing checked. Written up in `docs/presidio-comparison.md`.
*Exit: every synthetic case caught, nothing in the negative set falsely flagged, and the Presidio comparison written down.*

**3 — Pipeline and config.** Shared result shape, the runner, `rules.yaml` with named categories.
*Exit: redaction runs through the pipeline, reading rules from config.*

**4 — Proxy shell.** FastAPI app plus the provider adapter layer, shadow mode, and the `/check` try-it endpoint. No streaming yet.
*Exit: change `base_url` in a real script and it works end to end. `/check` returns a verdict on pasted text without contacting any provider.*

The `/check` endpoint is half a day's work and is the best demo the project will ever have — someone pastes a fake key into a single command and watches it get caught, having installed nothing. That's the moment a stranger decides whether to read the setup instructions.

**5 — Identity, tenancy and storage.** Per-key auth, tenant IDs and token counts on every record, database, every event logged. **The gateway holds the provider key** — developers get gateway keys, so routing around it means having no key at all. SQLite by default, Postgres by changing one URL. The verdict is recorded and the content never is.
*Exit: two different keys' traffic is distinguishable in the database, and tenant labels are on every row.* **Met**, and visible in `python -m gateway events --summary`.

Two things left open by Phase 5, both deliberate:

- **A failed authentication is not recorded.** Auth happens before the route runs, so there is no tenant to attribute the attempt to, and `events` requires one. Someone guessing keys is currently invisible. It needs either a nullable tenant on events or a table of its own, and the decision belongs with the dashboard.
- **`/health`, `/` and `/check` need no key.** `/check` is the demo — requiring a key would mean nobody can try the thing before deciding to run it — but a deployment that wants them private has no way to say so yet.

**6 — Docs, then ship V1.** A licence, a configuration reference, a guide to the rules file, an honest page of what the gateway does *not* protect against, and a container that runs it.
*Exit: someone follows the instructions on a clean machine without asking a single question.* **Met**, by cloning from GitHub into an empty directory on 9 October 2026 and running every command in the README. All of them worked — and the test still found a failure, because the first command in the README needed a virtualenv the README explained a hundred and twenty lines later. A broken order rather than a broken command, invisible to anyone who already had it working.

### V2 — "it catches attacks and shows you"

**7 — Injection detection, and the honest gate.** The semantic engine: patterns for the famous phrasings, embeddings for the paraphrases, capped at three windows. Also where the real evaluation harness gets built — hand-written attacks by family, public jailbreak datasets, and a separately written holdout — because this is where a score can genuinely fool you in a way a regex can't. Thresholds picked from score distributions.

*Exit, every line of it:*

- *a written go or no-go, based on the holdout set. If it doesn't separate real attacks from innocent questions, stop and rethink — don't tune past it.*
- *the same holdout scored by an off-the-shelf classifier, and both numbers published side by side.*
- *a false-alarm rate on the adversarial negative set, reported with the same weight as the catch rate.*
- *buried-injection cases in the holdout, so the report states a measured miss rate for text past the window cap rather than assuming one.*
- *the window cap recorded on the event when it is hit, visible in `gateway events`.*
- *the cap, and what it misses, written in `docs/what-it-protects.md`.*

**8 — Streaming.**
*Exit: streaming works, and a block partway through cuts off cleanly.*

**9 — Dashboard, reversible redaction, and cost tracking.** Login, live log of what got caught and by whom, settings for rules, spend per key. Placeholders restored in responses.
*Exit: someone who has never seen the project understands what it does within thirty seconds of looking at the screen. A redacted email round-trips and comes back intact.*

**10 — Docs, then ship V2.**

**Sizing, not dates.** Mark each phase as roughly a day, a few days, or a week. Calendar dates on a solo project become guilt rather than planning, and stop being useful the moment one slips.

---

## Working practices

**Semantic checks get a two-way data split.** One set to tune against, one locked away untouched until the end. Only the untouched score is believable. Tuning against the same cases you grade against is marking your own exam, and the resulting number means nothing. This applies to anything with a threshold. Regex doesn't need the *split*, because there is no threshold to tune — but it needs the other half of the idea just as much, and this was learned the hard way.

**Every phase closes with cases written without looking at the patterns.** The danger with regex is not a score that flatters you; it is a blind spot. You can only write a test for something you have thought of, so a set written alongside the code can confirm it still does what you already knew, and nothing more. Ten independently written cases at the end of Phase 2 found that UPI IDs had no pattern at all — not a weak one, none — which 110 cases written next to the code could never have shown. Over-redaction counts as a failure in that grading, not a curiosity.

**Keep a running friction-notes file** throughout the build. Every setup problem written down the moment it happens, and every bug worth understanding: what broke, why the obvious version of the code was wrong, how it was found, what fixed it, and what it taught. By the end everything works on your machine and you'll have forgotten what was hard.

Those notes do two jobs. They become the documentation — the difference between setup instructions that work for a stranger and ones that only work here. And they are what makes the project explainable long after it's built: add your own explanation of each bug, in your own words, alongside the detailed entry.

**Anything that must happen goes in a phase's exit criteria**, not in the prose above them. Prose is checked by nobody. The Presidio comparison was promised twice in this file and Phase 2 still closed without it, because the exit criteria listed only the test results — which passed.

**Setup and documentation get their own phase**, with real days attached. For a developer, bad setup instructions kill a good product. They don't file a bug — they close the tab.

---

## Later, not rejected

Names and addresses in redaction · session memory across conversation turns · malware-rephrase as an added category · checking the AI's answer on the way back · rate limiting per key or tenant · tenant admin screens and billing · a hosted version

The four-action shape, the named categories, the provider adapters and the tenant labels leave room for all of them.
