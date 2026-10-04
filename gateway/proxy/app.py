"""The HTTP surface of the gateway.

Thin on purpose. Nothing in here decides anything -- the engine decides, and
this layer only carries requests to it and answers back. Keeping decisions out
of the web layer is what lets the same checks run from the command line, from
tests, and from the proxy without being written three times.
"""

from fastapi import FastAPI

from gateway.console import use_utf8_output
from gateway.settings import settings

# Uvicorn imports this module to start the server, which makes it an entry
# point like any other -- and Windows consoles still default to cp1252.
# Phase 1's crash, one line of cure.
use_utf8_output()

app = FastAPI(
    title="AI Security Gateway",
    description="Self-hosted. Local. Nothing leaves.",
    version="0.4.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Is the gateway up, and what would it do with traffic right now?

    Deployment tooling polls this. It reports the mode as well as the status,
    because "up" isn't the interesting question: "up, in enforce mode" is a
    very different fact from "up, in shadow".
    """
    return {"status": "ok", "mode": settings.mode.value}
