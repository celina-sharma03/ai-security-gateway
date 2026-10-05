"""Runtime settings, read from environment variables and validated at startup.

Anything configurable lives here. Nothing reads os.environ directly.
"""

from enum import Enum
from pathlib import Path

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

    mode: Mode = Mode.SHADOW
    """Starts in shadow so a fresh install can never break anything."""

    rules_file: Path = PROJECT_ROOT / "gateway" / "config" / "rules.yaml"

    host: str = "127.0.0.1"
    port: int = 8080

    upstream_base_url: str = "https://api.openai.com/v1"
    """Where allowed requests get forwarded. Never hardcoded elsewhere."""

    upstream_timeout: float = 30.0
    """Seconds to wait for the provider. Long, because a model thinking hard
    about a long prompt is normal and cutting it off would be worse than
    waiting. Not unlimited, because a request that hangs forever holds a
    connection open forever."""


settings = Settings()
