"""The HTTP surface of the gateway.

Thin on purpose. Nothing in here decides anything -- the engine decides, and
this layer only carries requests to it and answers back. Keeping decisions out
of the web layer is what lets the same checks run from the command line, from
tests, and from the proxy without being written three times.
"""

from fastapi import FastAPI

from gateway.console import use_utf8_output
from gateway.engine.pipeline import build_pipeline
from gateway.engine.rules import load_rules
from gateway.proxy.schemas import CheckRequest, CheckResponse, FindingOut
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

# Built once at startup rather than per request. Re-reading and re-validating
# the rules file on every call would be wasted work, and a rules file with a
# mistake in it should stop the gateway starting -- not fail on the thousandth
# request, at night. The cost is that editing rules.yaml needs a restart, which
# is the honest trade for being predictable.
pipeline = build_pipeline(settings.mode, load_rules(settings.rules_file))


@app.get("/health")
def health() -> dict[str, str]:
    """Is the gateway up, and what would it do with traffic right now?

    Deployment tooling polls this. It reports the mode as well as the status,
    because "up" isn't the interesting question: "up, in enforce mode" is a
    very different fact from "up, in shadow".
    """
    return {"status": "ok", "mode": settings.mode.value}


@app.post("/check", response_model=CheckResponse)
def check(request: CheckRequest) -> CheckResponse:
    """What does the gateway see in this text?

    Contacts no provider and needs no API key -- this is the endpoint someone
    tries before they trust the thing with real traffic.
    """
    result = pipeline.run(request.text)

    # In shadow mode `result.text` is the original, because shadow changes
    # nothing. The checks still produced a redacted version, so take it from
    # them: this endpoint's job is to show what the gateway *saw*, whatever
    # the current mode would have done about it.
    redacted = next(
        (r.text for r in reversed(result.results) if r.text is not None),
        request.text,
    )

    return CheckResponse(
        mode=result.mode.value,
        decided=result.decided.value,
        action=result.action.value,
        categories=sorted(result.categories),
        redacted=redacted,
        findings=[
            FindingOut(
                category=finding.category,
                placeholder=finding.placeholder,
                start=finding.start,
                end=finding.end,
            )
            for check_result in result.results
            for finding in check_result.findings
        ],
    )
