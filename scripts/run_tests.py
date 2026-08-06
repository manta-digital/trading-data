#!/usr/bin/env python3
"""Run a test tier with an explicit, named set of environment variables.

The reviewed entry point for DB-touching test tiers. Two problems it solves,
both of which produced production incidents on 2026-08-04:

1. ``source .env`` is unusable in this project — a password containing ``$_``
   is mangled by shell expansion. The workaround that caused the incident was
   a runner that loaded ``.env`` with ``dotenv`` and injected *every* variable
   into the child, which handed ``MT_TIMESCALE_DB_URL`` to a fixture that then
   ``TRUNCATE``d six production tables.

2. A caller-side exclusion only protects the invocation that remembers it.

So this runner is **additive, not subtractive**: it starts from an explicit
per-tier allowlist and copies in only those names. Adding a variable to
``.env`` cannot widen a tier's access; that requires editing ``TIERS`` here,
in a reviewed file. The runtime scrub in ``test/conftest.py`` is the
independent second layer for anyone who bypasses this script entirely.

Usage:
    python scripts/run_tests.py unit
    python scripts/run_tests.py integration
    python scripts/run_tests.py load
    python scripts/run_tests.py integration -- -k migrations -x
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Per-tier environment allowlist. ADDITIVE — a tier gets exactly these names.
# MT_TIMESCALE_DB_URL (production) appears in no tier and must never be added;
# tests needing real schema use MT_TIMESCALE_TEST_URL + the ephemeral_db /
# migrated_db fixtures, which can only name a database the fixture created.
TIERS: dict[str, tuple[str, ...]] = {
    "unit": (),
    "integration": ("MT_TIMESCALE_TEST_URL",),
    "load": ("MT_TIMESCALE_TEST_URL", "MT_RUN_LOAD_TESTS"),
}

# Extra names each tier is allowed to pull from .env only if already present.
_ALWAYS_SAFE: tuple[str, ...] = ("MANTA_DATA_DIR",)


def load_dotenv_values(path: Path) -> dict[str, str]:
    """Parse ``.env`` without shell expansion (the ``$_`` password trap)."""
    if not path.exists():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def build_env(tier: str, dotenv: dict[str, str]) -> dict[str, str]:
    """Return the child environment: os.environ minus prod, plus allowlist."""
    allowed = TIERS[tier] + _ALWAYS_SAFE
    env = dict(os.environ)

    # Drop anything prod-shaped inherited from the parent shell. The conftest
    # scrub would catch this too; doing it here keeps the child honest even if
    # someone runs a tier whose conftest is not ours.
    env.pop("MT_TIMESCALE" + "_DB_URL", None)
    env.pop("MT_MARKET_DB_URL", None)

    for name in allowed:
        if name in dotenv:
            env[name] = dotenv[name]

    if tier == "load":
        env.setdefault("MT_RUN_LOAD_TESTS", "1")

    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tier", choices=sorted(TIERS))
    parser.add_argument(
        "pytest_args",
        nargs="*",
        help="extra args forwarded to pytest (after --)",
    )
    args = parser.parse_args()

    dotenv = load_dotenv_values(REPO_ROOT / ".env")
    env = build_env(args.tier, dotenv)

    supplied = [n for n in TIERS[args.tier] if n in env]
    print(f"tier={args.tier} env={supplied or '(none)'}", file=sys.stderr)

    cmd = [sys.executable, "-m", "pytest", f"test/{args.tier}", *args.pytest_args]
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
