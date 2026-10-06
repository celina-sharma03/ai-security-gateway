# AI Security Gateway

Stop your team pasting customer data into ChatGPT.

A self-hosted proxy that sits between anything using an LLM and the provider
it's calling. Before a prompt leaves your network, the gateway redacts personal
data and secrets and blocks attempts to manipulate the model. Everything is
recorded and attributed to whoever sent it.

You run it on your own infrastructure. Detection runs locally. The only thing
that ever leaves your network is the request to your provider — which was
already going there.

**Self-hosted. Local. Nothing leaves.**

---

## Status

**Phase 5 of 10 — a working proxy that knows whose traffic is whose.** It finds
and redacts personal data and secrets — cards, API keys, passwords, emails,
phone numbers, Aadhaar, PAN, IP addresses and UPI IDs — forwards what is left
to your provider, and records what happened without recording what was said.
Still to come: prompt-injection detection, a dashboard, and streaming (V2). See
[DECISIONS.md](DECISIONS.md) for the plan.

Try it without a provider, an account or a key:

```bash
.venv/Scripts/python.exe -m gateway check "my card is 4111 1111 1111 1111" --mode enforce
```

Or run the proxy and ask it what it sees:

```bash
.venv/Scripts/python.exe -m gateway serve
# then, in a browser: http://127.0.0.1:8080/docs
```

---

## How it works

Change one line in your application:

```diff
- base_url = "https://api.openai.com/v1"
+ base_url = "http://localhost:8080/v1"
```

Nothing else in your code changes.

Here is a real OpenAI client, talking through the gateway to a real model, with
that one line changed — [`examples/try_the_gateway.py`](examples/try_the_gateway.py):

```
what this script sent:
  my card is 4111 1111 1111 1111 and my email is priya@example.com

what the model says it received:
  [CREDIT_CARD_1],[EMAIL_1]

what the gateway did:
  x-gateway-mode          enforce
  x-gateway-action        redact
  x-gateway-categories    credit_card,email
```

The model listed the placeholders because the placeholders are all it was
given. It cannot name a card number it never received.

Works with OpenAI, Groq, Together, OpenRouter, a local Ollama — anything
OpenAI-compatible. Anthropic and Google are a new adapter file, not a rewrite.
Switching provider is a config change.

## Nobody needs the provider key

The gateway holds it. Your developers get gateway keys instead:

```bash
python -m gateway keys create --tenant "Billing" --label "priya's laptop"
# gw_live_K3n8Qx7fJ2mWp5RtYv9BzLcH4sNdA6gEuF1iO0jXyTk   — shown once
```

That is what makes this a control rather than advice. If developers hold the
provider key, anyone who finds the gateway inconvenient points their code
straight at the provider and nobody ever knows. If the gateway holds the only
key, there is nothing to route around it with.

Keys are stored as a hash and shown once, so a stolen copy of the database
contains no working credential. Revoking one is immediate, and a revoked key is
kept, because *"which key did this come from"* is the question asked after
something goes wrong.

## It records the verdict, never the content

```
$ python -m gateway events --summary

TENANT           REQUESTS  BLOCKED  REDACTED  FAILED    TOKENS
Billing                 4        0         4       3       349
Support                 5        0         5       3       561

What was caught:
  credit_card          9
  email                9
```

There is **no column for the prompt**, none for the reply, and none for the
values that were redacted — and a test asserts their absence, so adding one has
to be deliberate. A gateway that wrote every prompt into its own database would
have moved the problem rather than solved it: one breach would leak everything
anyone had ever typed.

Everything above was produced without a single card number being stored.

A blocked request comes back as a **normal, readable answer**, not an HTTP
error — your application does not crash, and the person reads why. The
provider's own errors are passed through untouched.

## What it doesn't protect against

Read [`docs/what-it-protects.md`](docs/what-it-protects.md) before deploying
this anywhere. The short version:

- **shadow mode is the default, and it changes nothing** — a gateway nobody has
  configured protects nothing
- **it cannot see meaning** — names, addresses and confidential prose have no
  shape to match
- **a bare number with no words around it is missed**, deliberately: the
  alternative redacts every invoice number in the company
- **prompt injection, streaming and checking the reply** are V2
- **it holds your provider key**, which is the point, and also means the
  gateway host is worth protecting

## Shadow mode

New installs start in **shadow mode**: the gateway checks everything and
records what it *would* have done, while traffic passes through untouched.

Run it for a week, read the log, fix any false alarms, then switch to
`enforce`. You never have to gamble production traffic on a filter you
haven't seen work.

---

## Development

Requires Python 3.12+. On Windows use `py`; `python` may not be on PATH.

```bash
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt

.venv/Scripts/python.exe -m alembic upgrade head   # create the database
.venv/Scripts/python.exe -m gateway                # show the configuration
.venv/Scripts/python.exe -m gateway serve          # run the proxy
.venv/Scripts/python.exe -m gateway keys list      # who has access
.venv/Scripts/python.exe -m gateway events         # what it has seen
.venv/Scripts/python.exe -m pytest                 # tests
.venv/Scripts/python.exe -m ruff check .           # lint
```

The database is SQLite by default — a file, nothing to install. The same code
runs on Postgres by changing `GATEWAY_DATABASE_URL`, which is what a team with
more than one gateway instance needs.

To see it end to end against a real model, get a free Groq key (no card). Note
which key goes where — that split is the whole point:

```powershell
# terminal 1 -- the gateway, which holds the provider key
$env:GATEWAY_UPSTREAM_BASE_URL = "https://api.groq.com/openai/v1"
$env:GATEWAY_UPSTREAM_API_KEY  = "gsk_..."
$env:GATEWAY_MODE = "enforce"
.venv/Scripts/python.exe -m gateway serve

# terminal 2 -- a developer, who has no provider key at all
.venv/Scripts/python.exe -m gateway keys create --tenant "Billing" --label "laptop"
$env:GATEWAY_KEY = "gw_live_..."
$env:GROQ_MODEL = "openai/gpt-oss-20b"
.venv/Scripts/python.exe examples/try_the_gateway.py
```

The test suite needs none of that — it runs offline against a stub provider.

On macOS and Linux the venv binary is at `.venv/bin/python` instead.

## Configuration

Everything is set through `GATEWAY_`-prefixed environment variables, or a
`.env` file. Detection rules live in
[`gateway/config/rules.yaml`](gateway/config/rules.yaml) — plain config, safe
to keep in your own version control.
