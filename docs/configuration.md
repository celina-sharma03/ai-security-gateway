# Configuration

Everything is set with environment variables, all prefixed `GATEWAY_`, or put
in a `.env` file next to the project. Nothing reads `os.environ` directly —
every setting lives in `gateway/settings.py`, is validated at startup, and is
wrong loudly rather than quietly.

To see what the gateway actually loaded:

```bash
python -m gateway
```

| variable | default | what it does |
| -------- | ------- | ------------ |
| `GATEWAY_MODE` | `shadow` | `shadow` or `enforce`. **Shadow changes nothing.** |
| `GATEWAY_UPSTREAM_BASE_URL` | `https://api.openai.com/v1` | where allowed requests go |
| `GATEWAY_UPSTREAM_API_KEY` | *(none)* | the provider key the gateway holds |
| `GATEWAY_PASSTHROUGH_PROVIDER_KEY` | `false` | let callers send their own key instead |
| `GATEWAY_UPSTREAM_TIMEOUT` | `30.0` | seconds to wait for the provider |
| `GATEWAY_DATABASE_URL` | `sqlite+aiosqlite:///./gateway.db` | where events and keys are stored |
| `GATEWAY_RULES_FILE` | `gateway/config/rules.yaml` | which rules file to load |
| `GATEWAY_HOST` | `127.0.0.1` | which address to listen on |
| `GATEWAY_PORT` | `8080` | which port |

## The two that decide whether it protects anything

### `GATEWAY_MODE`

```
shadow    every check runs, every finding is recorded, the original text is
          sent to the provider anyway
enforce   findings are acted on -- redacted, or blocked
```

**Shadow is the default and it is not a safe default by accident.** A filter
nobody has tested, put in front of production traffic, breaks somebody's work
on its first morning and gets switched off by lunchtime. Shadow lets you run a
week, read `python -m gateway events --summary`, find the false alarms, fix
them, and only then enforce.

It also means **a gateway nobody has configured protects nothing.** The startup
banner and `/health` both report the mode so this cannot be assumed.

### `GATEWAY_UPSTREAM_API_KEY`

The provider key, held by the gateway. This is what makes it a control: if
developers hold the provider key, anyone who finds the gateway inconvenient
points their code at the provider instead. If the gateway holds the only key,
there is nothing to route around it with.

Stored as a `SecretStr`, so printing the settings — in a banner, a log line, an
exception, a debugger — shows `**********`. It is never written to the
database, so a stolen copy of the database does not contain it.

**If it is not set and passthrough is off, every proxied request answers 500**
and says which variable is missing. That is deliberate: forwarding
unauthenticated would make the provider answer 401, and whoever read that 401
would spend an afternoon looking at their own key.

### `GATEWAY_PASSTHROUGH_PROVIDER_KEY`

Set to `true` and callers keep using their own provider key in `Authorization`,
sending their gateway key in `X-Gateway-Key` instead. The gateway identifies
them and checks their text, but never touches the credential.

Off by default, because a gateway that can be walked around protects nobody.
On for a team mid-migration with forty repositories to change.

## Storage

### `GATEWAY_DATABASE_URL`

```bash
sqlite+aiosqlite:///./gateway.db                        # default
postgresql+asyncpg://user:password@localhost/gateway    # a real server
```

SQLite is a file: nothing to install, nothing to run. **Two gateway instances
cannot share one**, so a deployment with more than one needs Postgres. The code
is identical either way; only this line changes.

After changing it, create the tables:

```bash
python -m alembic upgrade head
```

## Detection

### `GATEWAY_RULES_FILE`

Which rules file to load — see [`rules.md`](rules.md). Useful for keeping a
local file out of version control:

```bash
GATEWAY_RULES_FILE=./rules.local.yaml
```

A rules file with a mistake in it **stops the gateway starting**, with a
message saying what is wrong. An unknown category, an unknown action or a
broken regex is never ignored: the alternative is a gateway that silently
checks less than its operator believes.

## Networking

### `GATEWAY_HOST`

`127.0.0.1` by default, which accepts connections **from this machine only**.
That is the right default for trying it out and the wrong one for a container,
where nothing outside could ever reach it. In Docker, use `0.0.0.0`.

Listening on `0.0.0.0` means anything that can route to the machine can reach
the gateway, so that is the point at which the network it sits on starts to
matter.

### `GATEWAY_UPSTREAM_TIMEOUT`

Thirty seconds. Long, because a model thinking hard about a long prompt is
normal and cutting it off is worse than waiting. Not unlimited, because a
request that hangs forever holds a connection open forever.

A timeout answers the caller **504**; a provider that cannot be reached at all
answers **502**. Different facts for whoever is on call.

## A `.env` file

Copy [`.env.example`](../.env.example) to `.env` and edit it. `.env` is in
`.gitignore`; `.env.example` is committed and holds no secrets.

Anything already in the real environment wins over the file, which is what
makes a container's environment variables override a `.env` that got baked
into an image by accident.
