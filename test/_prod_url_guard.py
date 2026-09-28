"""Shared predicate for the per-tier prod-URL ratchet guards.

Not a test module (leading underscore): imported by
``test/integration/test_integration_prod_url_guard.py``,
``test/unit/test_unit_prod_url_guard.py`` and ``test/conftest.py``. Importable
because pytest prepends each conftest directory (including ``test/``) to
``sys.path``.

Also the one definition of which variables are production URLs. Since slice 923
that includes the two tick-database variables (D9): once a tick database exists
they name a production database, so the ratchet treats them exactly like the
primary URL, with an empty allowlist.

The predicate is a multiline-aware regex, not a per-line scan. The load
tier's original guard flags only lines containing both the variable name and
an environment-read marker — which misses the real-world form

    _DB_URL = os.environ.get(
        "MT_TIMESCALE_DB_URL",
        ...
    )

where the marker and the needle sit on different lines. Three unit-tier
fixtures wrote exactly that shape, and one of them emptied production
``universe_members`` on 2026-08-04 while the suite stayed green.
"""

from __future__ import annotations

import re
from pathlib import Path

# Needles concatenated so guard modules' own source cannot trip the check.
PRIMARY_URL_VAR = "MT_TIMESCALE" + "_DB_URL"
TICK_URL_VARS = ("MT_TICK" + "_DB_URL", "MT_TICK" + "_MAINTENANCE_URL")
PROD_URL_VARS = (PRIMARY_URL_VAR, *TICK_URL_VARS)


def _read_re(needles: tuple[str, ...]) -> re.Pattern[str]:
    """environ.get( / environ[ / getenv( followed (across newlines) by any needle.

    Docstring/comment mentions without an env read do not match.
    """
    names = "|".join(re.escape(n) for n in needles)
    return re.compile(
        r"(?:environ\s*\.\s*get|environ\s*\[|getenv\s*\()"
        r"[^)\]]*?[\"'](?:" + names + r")[\"']",
        re.DOTALL,
    )


def prod_url_readers(
    tier_dir: Path, needles: tuple[str, ...] = PROD_URL_VARS
) -> set[str]:
    """Tier-relative paths of ``*.py`` files that read any of ``needles``."""
    read_re = _read_re(needles)
    return {
        path.relative_to(tier_dir).as_posix()
        for path in tier_dir.rglob("*.py")
        if read_re.search(path.read_text(encoding="utf-8"))
    }


def assert_ratchet(
    tier_dir: Path, allowed: frozenset[str], needles: tuple[str, ...]
) -> None:
    """Fail on any new reader, and on any stale allowlist entry (shrink-only)."""
    readers = prod_url_readers(tier_dir, needles)
    what = ", ".join(needles)

    new = sorted(readers - allowed)
    assert not new, (
        f"New file(s) in {tier_dir.name}/ read {what}: {new}. "
        "Use MT_TIMESCALE_TEST_URL and the ephemeral_db/migrated_db fixtures "
        "(ephemeral_tick_db for the tick database) instead — the allowlist is "
        "shrink-only."
    )

    stale = sorted(allowed - readers)
    assert not stale, (
        f"Allowlist entries no longer read {what}: {stale}. "
        "Ratchet them out: delete the entries so they can never silently "
        "regress."
    )
