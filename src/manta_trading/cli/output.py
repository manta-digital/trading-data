"""Shared CLI output formatting — JSON and Rich text modes."""

from __future__ import annotations

import json
import sys

from rich import print as rprint
from rich.table import Table


def print_result(data: dict | list | str | Table, *, json_mode: bool) -> None:
    """Print command result to stdout.

    When *json_mode* is ``True``, serialise *data* as indented JSON (callers
    pass ``dict``/``list`` payloads in this mode). Otherwise the caller is
    responsible for Rich formatting before this point — this function simply
    passes *data* (including plain strings and Rich ``Table``s) through to
    Rich.
    """
    if json_mode:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        rprint(data)


def print_error(message: str, *, json_mode: bool) -> None:
    """Print an error message to stderr.

    JSON mode emits ``{"error": message}``; text mode uses Rich markup.
    """
    if json_mode:
        sys.stderr.write(json.dumps({"error": message}) + "\n")
    else:
        rprint(f"[red]Error: {message}[/red]", file=sys.stderr)


def make_table(title: str, columns: list[tuple[str, str]]) -> Table:
    """Create a Rich :class:`Table` with pre-configured columns.

    Each entry in *columns* is ``(header, style)``.
    """
    table = Table(title=title)
    for header, style in columns:
        table.add_column(header, style=style)
    return table
