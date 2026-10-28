"""``mt data --help`` groups commands by how an operator reaches for them (#57)."""

from __future__ import annotations

import re

from typer.main import get_command
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.help_layout import DATA_HELP_PANELS


def _data_group():
    return get_command(app).commands["data"]  # type: ignore[attr-defined]


def _help_text() -> str:
    result = CliRunner().invoke(app, ["data", "--help"], terminal_width=100)
    assert result.exit_code == 0, result.output
    return result.output


def test_every_registered_command_has_a_panel() -> None:
    registered = set(_data_group().commands)
    listed = {name for names in DATA_HELP_PANELS.values() for name in names}
    assert registered == listed


def test_panels_appear_in_table_order_with_everyday_reads_first() -> None:
    text = _help_text()
    titles = [t for t in DATA_HELP_PANELS if f"─ {t} " in text]
    assert titles == list(DATA_HELP_PANELS)
    positions = [text.index(f"─ {t} ") for t in DATA_HELP_PANELS]
    assert positions == sorted(positions)


def test_commands_within_a_panel_follow_table_order() -> None:
    text = _help_text()
    for names in DATA_HELP_PANELS.values():
        found = [
            m.group(1)
            for line in text.splitlines()
            if (m := re.match(r"│ (\S+)\s", line)) and m.group(1) in names
        ]
        assert found == list(names)
