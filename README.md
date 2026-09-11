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

**Phase 0 of 10 — skeleton only.** It runs and loads its config. It does not
check anything yet. See [DECISIONS.md](DECISIONS.md) for the full plan.

---

## How it will work

Change one line in your application:

```diff
- base_url = "https://api.openai.com/v1"
+ base_url = "http://localhost:8080/v1"
```

Nothing else in your code changes.

Works with OpenAI, Anthropic, Google, Azure, Ollama, and anything
OpenAI-compatible. Switching provider is a config change.

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

.venv/Scripts/python.exe -m gateway     # run it
.venv/Scripts/python.exe -m pytest      # tests
.venv/Scripts/python.exe -m ruff check .  # lint
```

On macOS and Linux the venv binary is at `.venv/bin/python` instead.

## Configuration

Everything is set through `GATEWAY_`-prefixed environment variables, or a
`.env` file. Detection rules live in
[`gateway/config/rules.yaml`](gateway/config/rules.yaml) — plain config, safe
to keep in your own version control.
