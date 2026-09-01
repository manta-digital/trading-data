"""Named symbol lists from ``config/symbol-lists.yaml`` (slice 146 Decision E).

Operator config, not application state — lists live in YAML under git
control. This module loads, validates, and resolves them; CLI and runner
call into it.

Schema:

    lists:
      priority1:
        description: "Top-N hand-picked"
        symbols: [SPY, QQQ, AAPL, ...]
      priority2:
        description: "S&P 500 snapshot"
        source: file:config/lists/sp500-snapshot.txt

Either ``symbols:`` (inline) or ``source: file:<rel-path>`` (file
reference), not both. ``source`` paths are resolved relative to the
config file's directory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    import psycopg

_logger = logging.getLogger(__name__)

_SOURCE_FILE_PREFIX: str = "file:"


class ListsConfigError(Exception):
    """Raised when ``symbol-lists.yaml`` is malformed or unreadable."""


class ListNotFoundError(KeyError):
    """Raised when a named list is not defined in the config."""


@dataclass(frozen=True)
class ListEntry:
    """Resolved view of a single named list."""

    name: str
    description: str
    symbols: list[str]


def load_lists(config_path: Path) -> dict[str, list[str]]:
    """Parse a symbol-lists YAML file and return ``name -> [symbols]``.

    Resolves ``source: file:...`` references relative to ``config_path.parent``.
    Raises ``ListsConfigError`` on any structural issue; never returns silently
    with an empty dict on a malformed file.

    To merge multiple config files (e.g. project + user), use
    ``load_lists_merged`` instead.
    """
    if not config_path.exists():
        raise ListsConfigError(f"symbol-lists config not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ListsConfigError(f"malformed YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict) or "lists" not in raw:
        raise ListsConfigError(
            f"{config_path}: top-level 'lists' key required; got {type(raw).__name__}"
        )

    lists_block = raw["lists"]
    if not isinstance(lists_block, dict):
        raise ListsConfigError(
            f"{config_path}: 'lists' must be a mapping of name -> entry"
        )

    out: dict[str, list[str]] = {}
    for name, entry in lists_block.items():
        out[name] = _resolve_entry(name, entry, config_path)
    return out


def _resolve_entry(name: str, entry: Any, config_path: Path) -> list[str]:
    if not isinstance(entry, dict):
        raise ListsConfigError(
            f"list '{name}': entry must be a mapping, got {type(entry).__name__}"
        )

    has_inline = "symbols" in entry
    has_source = "source" in entry
    if has_inline == has_source:
        raise ListsConfigError(
            f"list '{name}': must define exactly one of 'symbols' or 'source'"
        )

    if has_inline:
        symbols = entry["symbols"]
        if not isinstance(symbols, list) or not all(
            isinstance(s, str) for s in symbols
        ):
            raise ListsConfigError(
                f"list '{name}': 'symbols' must be a list of strings"
            )
        return [s.strip() for s in symbols if s.strip()]

    source = entry["source"]
    if not isinstance(source, str) or not source.startswith(_SOURCE_FILE_PREFIX):
        raise ListsConfigError(
            f"list '{name}': 'source' must be a string starting with "
            f"'{_SOURCE_FILE_PREFIX}'"
        )
    rel = source[len(_SOURCE_FILE_PREFIX) :]
    src_path = (config_path.parent / rel).resolve()
    if not src_path.exists():
        raise ListsConfigError(f"list '{name}': source file not found: {src_path}")
    return _read_symbol_file(src_path)


def _read_symbol_file(path: Path) -> list[str]:
    """Parse a symbols file: one ticker per non-blank, non-comment line."""
    out: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def resolve_list(name: str, config_path: Path) -> list[str]:
    """Return the resolved symbol list for ``name``.

    Raises ``ListNotFoundError`` if no list with that name exists. There is
    no silent fallback to "all active" — the caller chooses scope.
    """
    lists = load_lists(config_path)
    if name not in lists:
        known = sorted(lists.keys())
        raise ListNotFoundError(
            f"list '{name}' not defined in {config_path.name}; known: {known}"
        )
    return lists[name]


_USER_CONFIG_DIR: Path = Path.home() / ".config" / "manta-trading"
_USER_LISTS_CONFIG: Path = _USER_CONFIG_DIR / "symbol-lists.yaml"


def load_lists_merged(project_config: Path) -> dict[str, list[str]]:
    """Load lists from project config, then overlay the user config.

    Search order (later entries win on name conflicts):
    1. ``project_config`` (e.g. ``config/symbol-lists.yaml``)
    2. ``~/.config/manta-trading/symbol-lists.yaml`` (if it exists)

    A missing user config is silently ignored. A malformed user config
    raises ``ListsConfigError`` so the operator knows to fix it.
    """
    merged: dict[str, list[str]] = {}

    # Layer 1: project config (required).
    merged.update(load_lists(project_config))

    # Layer 2: user config (optional).
    if _USER_LISTS_CONFIG.exists():
        _logger.debug("loading user lists config: %s", _USER_LISTS_CONFIG)
        user_lists = load_lists(_USER_LISTS_CONFIG)
        if user_lists:
            _logger.debug(
                "user config adds/overrides lists: %s", sorted(user_lists.keys())
            )
        merged.update(user_lists)

    return merged


def resolve_list_merged(name: str, project_config: Path) -> list[str]:
    """Return the resolved symbol list for ``name`` from the merged config.

    Searches project config then user config; user config wins on conflicts.
    Raises ``ListNotFoundError`` if not found in either.
    """
    lists = load_lists_merged(project_config)
    if name not in lists:
        known = sorted(lists.keys())
        raise ListNotFoundError(
            f"list '{name}' not defined in project or user config; known: {known}"
        )
    return lists[name]


def intersect_with_active(
    symbols: list[str], conn: "psycopg.Connection[Any]"
) -> list[str]:
    """Filter ``symbols`` down to those active in ``instruments``.

    "Active" matches the daemon's scope: ``delisted_at_eodhd = false``.
    Symbols in the input list but absent from ``instruments`` are logged
    at WARNING (operator-visible: typo, dropped ticker, etc.). Order is
    preserved from the input list.
    """
    if not symbols:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM instruments "
            "WHERE delisted_at_eodhd = false AND symbol = ANY(%s)",
            (symbols,),
        )
        active: set[str] = {row[0] for row in cur.fetchall()}
    out: list[str] = []
    missing: list[str] = []
    for sym in symbols:
        if sym in active:
            out.append(sym)
        else:
            missing.append(sym)
    if missing:
        _logger.warning(
            "symbol-list: %d symbol(s) in list missing or delisted in instruments: %s",
            len(missing),
            ", ".join(missing[:10]) + ("..." if len(missing) > 10 else ""),
        )
    return out


def refresh_sp500(
    snapshot_path: Path,
    fetch_components: Callable[[], dict[str, Any]],
) -> int:
    """Refresh the S&P 500 snapshot file.

    ``fetch_components`` is a zero-arg callable that returns the parsed
    EODHD ``/fundamentals/GSPC.INDX`` payload. Inversion of control keeps
    the HTTP wiring out of this module (and trivially mockable).

    On malformed payload, raises and leaves the snapshot file untouched.
    Returns the number of tickers written on success.
    """
    payload = fetch_components()
    components = _extract_components(payload)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
    tmp_path.write_text("\n".join(components) + "\n")
    tmp_path.replace(snapshot_path)
    return len(components)


def _extract_components(payload: dict[str, Any]) -> list[str]:
    """Pull tickers from EODHD's ``/fundamentals/GSPC.INDX`` payload.

    Schema (per EODHD docs): ``{"Components": {"<n>": {"Code": "AAPL", ...}, ...}}``
    where ``<n>`` is a stringified index. We accept either that shape or a
    plain list under ``Components`` for resilience.
    """
    if not isinstance(payload, dict) or "Components" not in payload:
        raise ListsConfigError("EODHD GSPC.INDX response missing 'Components' key")
    block = payload["Components"]
    items: list[dict[str, Any]]
    if isinstance(block, dict):
        items = list(block.values())
    elif isinstance(block, list):
        items = block
    else:
        raise ListsConfigError(
            f"EODHD GSPC.INDX 'Components' has unexpected type: {type(block).__name__}"
        )

    out: list[str] = []
    for item in items:
        if not isinstance(item, dict) or "Code" not in item:
            raise ListsConfigError("EODHD GSPC.INDX component missing 'Code' field")
        code = item["Code"]
        if not isinstance(code, str) or not code.strip():
            raise ListsConfigError(
                "EODHD GSPC.INDX component 'Code' is empty or non-string"
            )
        out.append(code.strip())
    if not out:
        raise ListsConfigError("EODHD GSPC.INDX returned zero components")
    return out
