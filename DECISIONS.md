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

**One engine serves every semantic category.** Prompt injection and malware-rephrase detection are the same machinery — take incoming text, compare it semantically against known-bad examples. Same scoring, same thresholds. Only the examples in the config differ. Building injection detection *is* building the rephrase detector; adding malware afterwards is adding a category to a YAML file, not writing code.

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

**Rules live in a plain editable config file**, shipped with working defaults. It's *config, not code* — editing changes what the gateway looks for, never how it works. And the person editing is the operator, not the person being checked.

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
**Microsoft Presidio** — richer PII detection including names, and checksum validation on card numbers. Measured against plain regex rather than assumed better.
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

**1 — Test data.** Synthetic PII and secrets — cards, keys, emails, phones, Aadhaar, PAN — plus realistic cases that must *not* be caught. Ordinary tests, not a tune/holdout harness: a regex either matches or it doesn't, so there's no score to fool yourself with. Real PII can't be published, so this is generated rather than downloaded.
*Exit: a test suite that fails loudly when a pattern is wrong.*

**2 — Redaction engine.** Rigid patterns. Regex measured against Presidio.
*Exit: every synthetic case caught, and nothing in the negative set falsely flagged.*

**3 — Pipeline and config.** Shared result shape, the runner, `rules.yaml` with named categories.
*Exit: redaction runs through the pipeline, reading rules from config.*

**4 — Proxy shell.** FastAPI app plus the provider adapter layer, shadow mode, and the `/check` try-it endpoint. No streaming yet.
*Exit: change `base_url` in a real script and it works end to end. `/check` returns a verdict on pasted text without contacting any provider.*

The `/check` endpoint is half a day's work and is the best demo the project will ever have — someone pastes a fake key into a single command and watches it get caught, having installed nothing. That's the moment a stranger decides whether to read the setup instructions.

**5 — Identity, tenancy and storage.** Per-key auth, tenant IDs and token counts on every record, database, every event logged.
*Exit: two different keys' traffic is distinguishable in the database, and tenant labels are on every row.*

**6 — Docs, then ship V1.**
*Exit: someone follows the instructions on a clean machine without asking a single question.*

### V2 — "it catches attacks and shows you"

**7 — Injection detection, and the honest gate.** The semantic engine. Also where the real evaluation harness gets built — public jailbreak datasets, split into tune and holdout — because this is where a score can genuinely fool you in a way a regex can't. Thresholds picked from score distributions.
*Exit: a written go or no-go, based on the holdout set. If it doesn't separate real attacks from innocent questions, stop and rethink — don't tune past it.*

**8 — Streaming.**
*Exit: streaming works, and a block partway through cuts off cleanly.*

**9 — Dashboard, reversible redaction, and cost tracking.** Login, live log of what got caught and by whom, settings for rules, spend per key. Placeholders restored in responses.
*Exit: someone who has never seen the project understands what it does within thirty seconds of looking at the screen. A redacted email round-trips and comes back intact.*

**10 — Docs, then ship V2.**

**Sizing, not dates.** Mark each phase as roughly a day, a few days, or a week. Calendar dates on a solo project become guilt rather than planning, and stop being useful the moment one slips.

---

## Working practices

**Semantic checks get a two-way data split.** One set to tune against, one locked away untouched until the end. Only the untouched score is believable. Tuning against the same cases you grade against is marking your own exam, and the resulting number means nothing. This applies to anything with a threshold — it does *not* apply to regex, where a pattern either matches or it doesn't and ordinary tests are enough.

**Keep a running friction-notes file** throughout the build. Every setup problem written down the moment it happens — by the end everything works on your machine and you'll have forgotten what was hard. Those notes become the documentation.

**Setup and documentation get their own phase**, with real days attached. For a developer, bad setup instructions kill a good product. They don't file a bug — they close the tab.

---

## Later, not rejected

Names and addresses in redaction · session memory across conversation turns · malware-rephrase as an added category · checking the AI's answer on the way back · rate limiting per key or tenant · tenant admin screens and billing · a hosted version

The four-action shape, the named categories, the provider adapters and the tenant labels leave room for all of them.
