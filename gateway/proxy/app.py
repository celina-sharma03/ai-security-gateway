"""The HTTP surface of the gateway.

Thin on purpose. Nothing in here decides anything -- the engine decides, and
this layer only carries requests to it and answers back. Keeping decisions out
of the web layer is what lets the same checks run from the command line, from
tests, and from the proxy without being written three times.

The one route that matters is `/v1/chat/completions`, and the shape of it is
the whole product:

    read the request  ->  find the text  ->  run the checks
                      ->  block, or forward what the checks produced

Two things that are not here, deliberately. There is no decision about *what*
counts as sensitive: that is the engine's. And there is no knowledge of what a
request looks like: that is the provider adapter's. This file is the order of
events and nothing else.
"""

import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from gateway import __version__
from gateway.console import use_utf8_output
from gateway.engine.pipeline import Pipeline, build_pipeline
from gateway.engine.result import Action
from gateway.engine.rules import load_rules
from gateway.providers.openai import OpenAIProvider
from gateway.proxy.refusal import refusal_message
from gateway.proxy.schemas import CheckRequest, CheckResponse, FindingOut
from gateway.proxy.security import AuthError, require_key
from gateway.proxy.upstream import Upstream, UpstreamTimeout, UpstreamUnreachable
from gateway.settings import settings
from gateway.storage.models import ApiKey

# Uvicorn imports this module to start the server, which makes it an entry
# point like any other -- and Windows consoles still default to cp1252.
# Phase 1's crash, one line of cure.
use_utf8_output()

# Built once at startup rather than per request. Re-reading and re-validating
# the rules file on every call would be wasted work, and a rules file with a
# mistake in it should stop the gateway starting -- not fail on the thousandth
# request, at night. The cost is that editing rules.yaml needs a restart, which
# is the honest trade for being predictable.
pipeline = build_pipeline(settings.mode, load_rules(settings.rules_file))

provider = OpenAIProvider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One HTTP client for the life of the process, closed on the way out.

    A client per request would mean a new TCP connection and a new TLS
    handshake every time -- slower than the checks it is waiting behind.
    """
    app.state.upstream = Upstream()
    yield
    await app.state.upstream.close()


app = FastAPI(
    title="AI Security Gateway",
    description="Self-hosted. Local. Nothing leaves.",
    version=__version__,
    lifespan=lifespan,
)


def get_pipeline() -> Pipeline:
    """Indirection with a purpose: a test overrides this to run the same route
    in enforce mode without starting a second application."""
    return pipeline


def get_upstream(request: Request) -> Upstream:
    return request.app.state.upstream


def error_body(message: str, kind: str) -> dict:
    """An error in the provider's own shape, so a client library can read it
    the way it reads any other error."""
    return {"error": {"message": message, "type": kind, "param": None, "code": None}}


@app.exception_handler(AuthError)
async def unauthorized(request: Request, exc: AuthError) -> JSONResponse:
    """401 in the provider's own error shape.

    An HTTP error is right here, unlike a block: a blocked request is the
    gateway working, while a missing key is the caller having made a mistake
    they need to fix. Client libraries raise an authentication error on this,
    which is exactly what should happen.
    """
    return JSONResponse(
        status_code=401,
        content=error_body(exc.message, "invalid_api_key"),
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.get("/")
def root() -> dict[str, object]:
    """What a person gets for opening the gateway in a browser.

    It used to be a 404, which looks like a broken deployment rather than a
    working one. Someone who has just changed their base_url and mistyped the
    path needs to know they reached the right machine.

    Deliberately says nothing about the mode or the rules. `/health` reports
    the mode because deployment tooling needs it, and in V1 neither endpoint
    has any authentication -- so the less an unauthenticated caller learns
    about how enforcement is configured, the better. Putting these behind the
    per-key auth that arrives in Phase 5 is on the list.
    """
    return {
        "name": "AI Security Gateway",
        "version": app.version,
        "description": "Self-hosted. Local. Nothing leaves.",
        "endpoints": {
            "/v1/chat/completions": "the proxy -- point your client's base_url here",
            "/check": "what does the gateway see in this text? no provider contacted",
            "/health": "is it up, and in which mode",
            "/docs": "the API, documented and clickable",
        },
    }


@app.get("/health")
def health() -> dict[str, str]:
    """Is the gateway up, and what would it do with traffic right now?

    Deployment tooling polls this. It reports the mode as well as the status,
    because "up" isn't the interesting question: "up, in enforce mode" is a
    very different fact from "up, in shadow".
    """
    return {"status": "ok", "mode": settings.mode.value}


@app.post("/check", response_model=CheckResponse)
def check(
    request: CheckRequest,
    pipeline: Annotated[Pipeline, Depends(get_pipeline)],
) -> CheckResponse:
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


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    key: Annotated[ApiKey, Depends(require_key)],
    pipeline: Annotated[Pipeline, Depends(get_pipeline)],
    upstream: Annotated[Upstream, Depends(get_upstream)],
) -> Response:
    """The proxy. Someone's application thinks this is OpenAI.

    The body is read as a plain dict rather than a validated model on purpose.
    The provider owns its own API, and new fields appear in it constantly; a
    gateway that rejected a request for carrying a field it hadn't heard of
    would break working applications for no benefit. So unknown fields travel
    through untouched, and the provider validates its own requests.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content=error_body("the request body is not valid JSON", "invalid_request_error"),
        )

    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content=error_body("the request body must be a JSON object", "invalid_request_error"),
        )

    # Each message is checked on its own rather than as one joined blob, so a
    # finding's offsets still point at real characters in a real message, and
    # the rebuilt request can never put one person's text into another's.
    texts = provider.texts(body)
    results = [pipeline.run(text) for text in texts]

    decided = max((result.decided for result in results), default=Action.ALLOW)
    action = max((result.action for result in results), default=Action.ALLOW)
    categories = sorted({category for result in results for category in result.categories})

    # The verdict, for anything reading by machine. The body carries the
    # provider's answer, so this is the only place a client can see what the
    # gateway did without being told.
    headers = {
        "X-Gateway-Mode": settings.mode.value,
        "X-Gateway-Decided": decided.value,
        "X-Gateway-Action": action.value,
        "X-Gateway-Categories": ",".join(categories),
        "X-Gateway-Tenant": key.tenant.name,
    }

    # `action` is already mode-aware: in shadow mode the pipeline turns a block
    # into a log, so this branch is simply never taken there. The mode logic
    # lives in the pipeline and is not repeated here.
    if action is Action.BLOCK:
        return JSONResponse(
            status_code=200,
            content=provider.blocked_response(body, refusal_message(categories)),
            headers=headers,
        )

    # Same again: `result.text` is what the pipeline says should go onward --
    # the redacted text in enforce mode, the original in shadow.
    onward = provider.with_texts(body, [result.text for result in results]) if results else body

    # The gateway key must not travel onward. Sending `gw_live_...` to the
    # provider would write a working credential for this gateway into a third
    # party's logs. Step 4 puts the real provider key here instead; until then
    # the request goes on with no Authorization at all, which is honest -- it
    # will be refused by anyone who needs one.
    outgoing = dict(request.headers)
    if getattr(request.state, "strip_authorization", False):
        outgoing.pop("authorization", None)

    try:
        sent = await upstream.send("/chat/completions", onward, outgoing)
    except UpstreamTimeout as exc:
        return JSONResponse(
            status_code=504, content=error_body(str(exc), "upstream_timeout"), headers=headers
        )
    except UpstreamUnreachable as exc:
        return JSONResponse(
            status_code=502, content=error_body(str(exc), "upstream_unreachable"), headers=headers
        )

    # The provider's answer, passed back as it came: its status code, its body,
    # its content type. A 429 stays a 429, because the caller needs the
    # provider's answer and not one of ours invented on top of it.
    return Response(
        content=sent.body,
        status_code=sent.status_code,
        media_type=sent.media_type,
        headers=headers,
    )
