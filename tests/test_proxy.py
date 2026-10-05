"""Phase 4: the HTTP surface.

These go through the real FastAPI app rather than calling the functions
underneath, and that is the point. The engine is already tested to death. What
is untested is the wiring: does a JSON body reach the pipeline, and does the
answer come back in a shape a client can actually read?

TestClient runs the app in-process -- no server, no port, no network -- which
is why adding these costs the suite almost no time.
"""

from fastapi.testclient import TestClient

from gateway.proxy.app import app
from gateway.settings import settings

client = TestClient(app)

CARD = "my card is 4111 1111 1111 1111"


def test_health_reports_the_status_and_the_mode():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": settings.mode.value}


def test_check_catches_a_card_and_shows_the_redacted_text():
    response = client.post("/check", json={"text": CARD})
    body = response.json()

    assert response.status_code == 200
    assert body["decided"] == "redact"
    assert body["categories"] == ["credit_card"]
    assert body["redacted"] == "my card is [CREDIT_CARD_1]"


def test_check_leaves_ordinary_text_alone():
    body = client.post("/check", json={"text": "how do I center a div"}).json()

    assert body["decided"] == "allow"
    assert body["action"] == "allow"
    assert body["categories"] == []
    assert body["findings"] == []


def test_the_response_never_repeats_the_original_value():
    """The one test in this file that is about security rather than wiring.

    The engine knows the card number -- it has to, in order to find it. The
    HTTP layer must never say it back. Checked against the whole response body
    as raw text rather than field by field, so a field added later cannot
    quietly leak it.
    """
    response = client.post("/check", json={"text": CARD})

    assert "4111" not in response.text


def test_empty_text_is_rejected():
    """Pydantic's min_length, enforced by FastAPI before our code runs. 422 is
    the standard answer for a body that didn't validate."""
    assert client.post("/check", json={"text": ""}).status_code == 422


def test_the_root_says_what_this_is_and_where_to_go():
    """It used to be a 404, which looks like a broken deployment rather than a
    working one."""
    response = client.get("/")
    body = response.json()

    assert response.status_code == 200
    assert body["name"] == "AI Security Gateway"
    assert "/v1/chat/completions" in body["endpoints"]
    assert "/check" in body["endpoints"]


def test_the_root_does_not_advertise_the_mode():
    """/health reports the mode because deployment tooling needs it. The root
    does not, because in V1 neither endpoint has any authentication and an
    unauthenticated caller does not need to know whether enforcement is on."""
    assert "mode" not in client.get("/").json()


def test_the_api_reports_the_same_version_as_the_package():
    """Caught live: the CLI said 0.1.0 while the API said 0.4.0, because the
    version was hardcoded into the FastAPI app. One source, and a test so the
    two cannot drift apart again."""
    from gateway import __version__

    assert client.get("/").json()["version"] == __version__
