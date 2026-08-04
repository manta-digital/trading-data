"""Generate (or verify) the committed OpenAPI artifact at docs/api/openapi.json.

Schema generation calls ``create_app().openapi()`` and never enters the
lifespan hook, so **no database and no MT_TIMESCALE_DB_URL are required** — the
artifact can be regenerated on any checkout (slice 186 D7).

Run:
    uv run python scripts/dump_openapi.py            # write the artifact
    uv run python scripts/dump_openapi.py --check    # fail on drift, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from manta_trading.api_server.app import create_app

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.json"
"""Committed location. Referenced by the README and by the drift test."""


def generate() -> str:
    """Return the serialized schema exactly as it is written to disk."""
    schema: dict[str, Any] = create_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against the committed artifact instead of writing it",
    )
    args = parser.parse_args(argv)

    generated = generate()

    if args.check:
        if not ARTIFACT_PATH.exists():
            print(f"missing artifact: {ARTIFACT_PATH}", file=sys.stderr)
            return 1
        if ARTIFACT_PATH.read_text(encoding="utf-8") != generated:
            print(
                f"{ARTIFACT_PATH} is out of date; "
                "run: uv run python scripts/dump_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"{ARTIFACT_PATH} is up to date")
        return 0

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(generated, encoding="utf-8")
    print(f"wrote {ARTIFACT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
