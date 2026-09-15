"""`python -m gateway check`: the pipeline from the command line."""

from gateway.__main__ import main

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
