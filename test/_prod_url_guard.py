"""Shared predicate for the per-tier prod-URL ratchet guards.

Not a test module (leading underscore): imported by
``test/integration/test_prod_url_guard.py`` and
``test/unit/test_prod_url_guard.py``. Importable because pytest prepends each
conftest directory (including ``test/``) to ``sys.path``.

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

# Needle concatenated so guard modules' own source cannot trip the check.
_PROD_URL_VAR = "MT_TIMESCALE" + "_DB_URL"

# environ.get( / environ[ / getenv( followed (across newlines) by the quoted
# variable name. Docstring/comment mentions without an env read do not match.
_READ_RE = re.compile(
    r"(?:environ\s*\.\s*get|environ\s*\[|getenv\s*\()"
    r"[^)\]]*?[\"']" + _PROD_URL_VAR + r"[\"']",
    re.DOTALL,
)


def prod_url_readers(tier_dir: Path) -> set[str]:
    """Tier-relative paths of ``*.py`` files that read the production URL."""
    return {
        path.relative_to(tier_dir).as_posix()
        for path in tier_dir.rglob("*.py")
        if _READ_RE.search(path.read_text(encoding="utf-8"))
    }


def assert_ratchet(tier_dir: Path, allowed: frozenset[str]) -> None:
    """Fail on any new reader, and on any stale allowlist entry (shrink-only)."""
    readers = prod_url_readers(tier_dir)

    new = sorted(readers - allowed)
    assert not new, (
        f"New file(s) in {tier_dir.name}/ read {_PROD_URL_VAR}: {new}. "
        "Use MT_TIMESCALE_TEST_URL and the ephemeral_db/migrated_db fixtures "
        "instead — the allowlist is shrink-only."
    )

    stale = sorted(allowed - readers)
    assert not stale, (
        f"Allowlist entries no longer read {_PROD_URL_VAR}: {stale}. "
        "Ratchet them out: delete the entries so they can never silently "
        "regress."
    )
