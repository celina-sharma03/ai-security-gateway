"""`python -m gateway`: the pipeline and the server from the command line."""

from gateway.__main__ import main
from gateway.settings import settings

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
