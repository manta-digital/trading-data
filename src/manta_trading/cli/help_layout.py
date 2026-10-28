"""Command order and grouping for ``mt data --help`` (#57).

One table is the only place that says where a command sits: panel order is
dict order, command order inside a panel is tuple order. Adding a command to
``data`` without listing it here fails loudly when the help is built.
"""

from __future__ import annotations

import click
from typer.core import TyperGroup

#: Panel title -> its commands. Everyday reads first, one-off repair last.
DATA_HELP_PANELS: dict[str, tuple[str, ...]] = {
    "Everyday": ("overview", "status", "health", "get", "accounting"),
    "Acquisition": ("pull", "extend", "daemon", "kalshi", "tick", "off-tape"),
    "Reference data": (
        "instruments",
        "calendars",
        "lists",
        "ca",
        "universes",
        "caggs",
        "migrate",
    ),
    "Maintenance": ("init", "rechunk", "restore"),
}

_PANEL_OF = {name: panel for panel, names in DATA_HELP_PANELS.items() for name in names}
_ORDER = [name for names in DATA_HELP_PANELS.values() for name in names]


class DataHelpGroup(TyperGroup):
    """A group whose help lists commands per :data:`DATA_HELP_PANELS`."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        unlisted = set(self.commands) - set(_ORDER)
        if unlisted:
            raise RuntimeError(
                f"commands missing from DATA_HELP_PANELS: {sorted(unlisted)}"
            )
        names = [name for name in _ORDER if name in self.commands]
        for name in names:
            # Typer's help renderer reads the panel off the command object.
            self.commands[name].rich_help_panel = _PANEL_OF[name]  # type: ignore[attr-defined]
        return names
