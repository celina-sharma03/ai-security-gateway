"""Entry point. Phase 0: proves the wiring works.

Later phases replace this with the uvicorn server.
"""

from gateway import __version__
from gateway.settings import settings


def main() -> None:
    print(f"AI Security Gateway {__version__}")
    print(f"  mode:     {settings.mode.value}")
    print(f"  rules:    {settings.rules_file}")
    print(f"  upstream: {settings.upstream_base_url}")
    print(f"  listen:   {settings.host}:{settings.port}")

    if not settings.rules_file.exists():
        print(f"\n  WARNING: rules file not found at {settings.rules_file}")


if __name__ == "__main__":
    main()
