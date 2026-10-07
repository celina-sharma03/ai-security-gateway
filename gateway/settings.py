"""Runtime settings, read from environment variables and validated at startup.

Anything configurable lives here. Nothing reads os.environ directly.
"""

from enum import Enum
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Mode(str, Enum):
    """How the gateway acts on what its checks find.

    SHADOW turns every block and redact into a log. The gateway still
    checks everything and records what it would have done, but traffic
    passes through untouched. This is how someone builds confidence
    before putting an untested filter in front of real traffic.
    """

    SHADOW = "shadow"
    ENFORCE = "enforce"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, values: object) -> object:
        """A variable left empty means "I have not filled this in".

        Found while writing .env.example. Someone copies it, leaves

            GATEWAY_UPSTREAM_API_KEY=

        and without this the gateway reads that as a key -- an empty one. It
        then believes it is configured, sends `Authorization: Bearer ` to the
        provider, and the provider answers 401. Whoever reads that 401 spends
        an hour on a provider key that was never the problem, instead of
        seeing the gateway say plainly that it has no key.

        The same applies to every other setting: a blank GATEWAY_MODE should
        fall back to the default, not fail validation with a pydantic
        traceback on a line nobody meant to write.
        """
        if isinstance(values, dict):
            return {
                name: value
                for name, value in values.items()
                if not (isinstance(value, str) and not value.strip())
            }
        return values

    mode: Mode = Mode.SHADOW
    """Starts in shadow so a fresh install can never break anything."""

    rules_file: Path = PROJECT_ROOT / "gateway" / "config" / "rules.yaml"

    host: str = "127.0.0.1"
    port: int = 8080

    upstream_base_url: str = "https://api.openai.com/v1"
    """Where allowed requests get forwarded. Never hardcoded elsewhere."""

    upstream_api_key: SecretStr | None = None
    """The provider's key, held by the gateway rather than by the callers.

    This is what turns the gateway from advice into a control. If developers
    hold the provider key, anyone who finds the gateway inconvenient points
    their code straight at the provider and nobody ever knows. If the gateway
    holds the only key, routing around it means having no key at all.

    SecretStr rather than str so that printing the settings -- in a startup
    banner, a log line, an exception, a debugger -- shows `**********` and not
    the key. The value is only reachable by asking for it explicitly, with
    `.get_secret_value()`, which is a thing you have to mean.

    It lives in the environment and never in the database, so a stolen copy of
    the database does not include it.
    """

    passthrough_provider_key: bool = False
    """Let callers keep using their own provider key, and identify themselves
    with `X-Gateway-Key` instead.

    Off by default, because a gateway that can be walked around protects
    nobody. On for a team migrating, who have forty repositories to change and
    need the gateway working before the key moves.
    """

    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'gateway.db'}"
    """SQLite by default: a file, no server, nothing to install. Anyone can
    clone this and have it running before they would have finished reading a
    Postgres connection string.

    The same code runs on Postgres by changing this one line:

        postgresql+asyncpg://user:password@localhost/gateway

    which is what a team with more than one gateway instance needs, because
    two instances cannot share a SQLite file."""

    upstream_timeout: float = 30.0
    """Seconds to wait for the provider. Long, because a model thinking hard
    about a long prompt is normal and cutting it off would be worse than
    waiting. Not unlimited, because a request that hangs forever holds a
    connection open forever."""


settings = Settings()
