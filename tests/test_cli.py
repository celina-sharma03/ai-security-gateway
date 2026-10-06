"""`python -m gateway`: the pipeline, the server and the keys, from the
command line."""

import asyncio
import pathlib

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from gateway.__main__ import main
from gateway.settings import settings
from gateway.storage import database
from gateway.storage.models import Base

CARD = "my card is 4111 1111 1111 1111"


def test_check_in_enforce_mode_shows_the_redacted_text(capsys):
    assert main(["check", CARD, "--mode", "enforce"]) == 0

    out = capsys.readouterr().out
    assert "[CREDIT_CARD_1]" in out
    assert "redact" in out


def test_check_in_shadow_mode_sends_the_original(capsys):
    assert main(["check", CARD, "--mode", "shadow"]) == 0

    out = capsys.readouterr().out
    assert CARD in out
    assert "[CREDIT_CARD_1]" not in out


def test_check_uses_the_rules_file_it_is_given(tmp_path, capsys):
    rules = tmp_path / "rules.yaml"
    rules.write_text("categories:\n  credit_card: block\n", encoding="utf-8")

    assert main(["check", CARD, "--mode", "enforce", "--rules", str(rules)]) == 0
    assert "blocked" in capsys.readouterr().out


def test_a_broken_rules_file_stops_with_a_clear_message(tmp_path, capsys):
    rules = tmp_path / "rules.yaml"
    rules.write_text("categories:\n  phnoe: off\n", encoding="utf-8")

    assert main(["check", CARD, "--rules", str(rules)]) == 2
    assert "phnoe" in capsys.readouterr().err


# --- `python -m gateway serve` ------------------------------------------
# uvicorn.run blocks forever, so these replace it and check what it was
# asked to do. Starting a real server in a test would hang the suite.


def _fake_uvicorn(monkeypatch) -> dict:
    captured: dict = {}

    def run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", run)
    return captured


def test_serve_listens_where_the_settings_say(monkeypatch):
    captured = _fake_uvicorn(monkeypatch)

    assert main(["serve"]) == 0

    assert captured["app"] == "gateway.proxy.app:app"
    assert captured["host"] == settings.host
    assert captured["port"] == settings.port
    assert captured["reload"] is False


def test_serve_flags_win_over_the_settings(monkeypatch):
    captured = _fake_uvicorn(monkeypatch)

    assert main(["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"]) == 0

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
    assert captured["reload"] is True


def test_serve_says_where_it_will_be_before_starting(monkeypatch, capsys):
    """Printed before uvicorn takes over the terminal, or nobody ever sees it."""
    _fake_uvicorn(monkeypatch)

    main(["serve", "--port", "9000"])

    out = capsys.readouterr().out
    assert "http://127.0.0.1:9000/v1" in out
    assert settings.upstream_base_url in out


# --- `python -m gateway keys` -------------------------------------------


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    """Point the gateway at a throwaway database file for one test.

    The CLI reaches for the real gateway.db, so without this a test run would
    write keys into it.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    async def build() -> None:
        await database.reset()
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(build())
    yield url
    asyncio.run(database.reset())


def issued(capsys) -> str:
    """The key the CLI just printed."""
    for word in capsys.readouterr().out.split():
        if word.startswith("gw_live_"):
            return word
    raise AssertionError("no key was printed")


def test_creating_a_key_shows_it_once_and_says_so(temp_database, capsys):
    assert main(["keys", "create", "--tenant", "Billing", "--label", "CI"]) == 0

    out = capsys.readouterr().out
    assert "gw_live_" in out
    assert "only time it will be shown" in out


def test_the_created_key_is_not_in_the_database(temp_database, capsys):
    """The same promise as the unit test, checked through the real command and
    against the real file: whatever was printed, the file does not contain it."""
    main(["keys", "create", "--tenant", "Billing", "--label", "CI"])
    key = issued(capsys)

    path = temp_database.removeprefix("sqlite+aiosqlite:///")
    assert key.encode() not in pathlib.Path(path).read_bytes()


def test_listing_keys_shows_the_tenant_and_the_visible_part(temp_database, capsys):
    main(["keys", "create", "--tenant", "Billing", "--label", "CI"])
    key = issued(capsys)

    assert main(["keys", "list"]) == 0

    out = capsys.readouterr().out
    assert "Billing" in out
    assert key[:16] in out
    assert key not in out
    assert "active" in out


def test_listing_nothing_explains_how_to_make_one(temp_database, capsys):
    assert main(["keys", "list"]) == 0
    assert "keys create" in capsys.readouterr().out


def test_revoking_a_key_reports_it(temp_database, capsys):
    main(["keys", "create", "--tenant", "Billing", "--label", "old laptop"])
    key = issued(capsys)

    assert main(["keys", "revoke", key[:16]]) == 0

    out = capsys.readouterr().out
    assert "Revoked" in out
    assert "old laptop" in out

    main(["keys", "list"])
    assert "revoked" in capsys.readouterr().out


def test_revoking_a_key_that_is_not_there_fails(temp_database, capsys):
    """Exit code 1, not 0. A command that reports success for a key it never
    found is how someone walks away believing they cut off access they didn't."""
    assert main(["keys", "revoke", "gw_live_nothing"]) == 1
    assert "No key in use" in capsys.readouterr().err


def test_a_missing_database_says_how_to_create_it(tmp_path, monkeypatch, capsys):
    """The first five minutes: a stranger runs `keys create` before migrating.
    A SQLAlchemy OperationalError traceback would be a poor answer."""
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'none.db'}")
    asyncio.run(database.reset())

    assert main(["keys", "create", "--tenant", "Billing"]) == 2
    assert "alembic upgrade head" in capsys.readouterr().err

    asyncio.run(database.reset())
