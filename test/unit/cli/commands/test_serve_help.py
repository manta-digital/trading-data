"""Tests for `mt serve --help` text (slice 916).

The --workers help used to point at deprecated slice 155 as "the future
supervised launcher"; supervision is real now, via the mt-serve systemd
unit. User-facing help must name the unit and cite no slice numbers.
"""

from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


def _normalized_help() -> str:
    """Help output with wrapping collapsed, so phrases match across lines."""
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    return " ".join(result.output.split())


def test_help_names_the_systemd_unit() -> None:
    assert "mt-serve" in _normalized_help()


def test_help_cites_no_slice_number() -> None:
    assert "slice" not in _normalized_help().lower()
