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

**Phase 4 of 10 — it is a working proxy.** It finds and redacts personal data
and secrets — cards, API keys, passwords, emails, phone numbers, Aadhaar, PAN,
IP addresses and UPI IDs — and forwards what is left to your provider. Still to
come: per-key identity and storage (Phase 5), prompt-injection detection, a
dashboard, and streaming (V2). See [DECISIONS.md](DECISIONS.md) for the plan.

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

A blocked request comes back as a **normal, readable answer**, not an HTTP
error — your application does not crash, and the person reads why. The
provider's own errors are passed through untouched.

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

.venv/Scripts/python.exe -m gateway       # show the configuration
.venv/Scripts/python.exe -m gateway serve # run the proxy
.venv/Scripts/python.exe -m pytest        # tests
.venv/Scripts/python.exe -m ruff check .  # lint
```

To see it end to end against a real model, get a free Groq key (no card) and:

```powershell
$env:GROQ_API_KEY = "gsk_..."
$env:GROQ_MODEL = "openai/gpt-oss-20b"
$env:GATEWAY_UPSTREAM_BASE_URL = "https://api.groq.com/openai/v1"
$env:GATEWAY_MODE = "enforce"

.venv/Scripts/python.exe -m gateway serve            # one terminal
.venv/Scripts/python.exe examples/try_the_gateway.py # another
```

The test suite needs none of that — it runs offline against a stub provider.

On macOS and Linux the venv binary is at `.venv/bin/python` instead.

## Configuration

Everything is set through `GATEWAY_`-prefixed environment variables, or a
`.env` file. Detection rules live in
[`gateway/config/rules.yaml`](gateway/config/rules.yaml) — plain config, safe
to keep in your own version control.
