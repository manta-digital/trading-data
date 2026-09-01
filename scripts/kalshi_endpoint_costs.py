#!/usr/bin/env python3
"""Print Kalshi's per-endpoint request costs (slice 267, Task 1.1).

A read-only call to ``GET /account/endpoint_costs`` — the one unknown the
267 design asked to be read before the historical phase is built (design
*Risks*, first item). The endpoint is authenticated: it 401s in public mode,
so the script refuses to run without a key pair rather than making a call
that cannot succeed.

Usage (on the host, where the key is installed)::

    sudo uv run python scripts/kalshi_endpoint_costs.py \
        --env-file /etc/manta-trading.env

The body is printed as JSON, verbatim. Read once and recorded in the design
(Task 1.3); nothing under ``data/kalshi`` uses this endpoint, so its path is
a constant here rather than in ``constants.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from manta_trading.config import Settings
from manta_trading.data.kalshi.auth import KalshiCredentialError, load_credentials
from manta_trading.data.kalshi.constants import (
    KALSHI_API_KEY_ID_ENV,
    KALSHI_PRIVATE_KEY_PATH_ENV,
)
from manta_trading.data.kalshi.transport import KalshiTransport
from manta_trading.providers.errors import ProviderError

DESCRIPTION = "Print Kalshi's per-endpoint request costs as JSON."
#: Authenticated endpoint; read once, so it lives here (see module docstring).
ENDPOINT_COSTS_PATH = "/account/endpoint_costs"
EXIT_NO_KEY = 2
EXIT_PROVIDER_ERROR = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="settings file holding the Kalshi key pair (e.g. /etc/manta-trading.env)",
    )
    return parser.parse_args(argv)


async def fetch_costs(env_file: Path) -> object:
    """Load the key pair from ``env_file`` and GET the endpoint costs."""
    settings = Settings(_env_file=env_file)
    credentials = load_credentials(
        settings.kalshi_api_key_id, settings.kalshi_private_key_path
    )
    if credentials is None:
        raise KalshiCredentialError(
            f"{ENDPOINT_COSTS_PATH} is authenticated: {env_file} sets neither "
            f"{KALSHI_API_KEY_ID_ENV} nor {KALSHI_PRIVATE_KEY_PATH_ENV}"
        )
    transport = KalshiTransport(credentials=credentials)
    try:
        return await transport.get_json(ENDPOINT_COSTS_PATH, {})
    finally:
        await transport.aclose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        body = asyncio.run(fetch_costs(args.env_file))
    except KalshiCredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_KEY
    except ProviderError as exc:
        print(f"error: {ENDPOINT_COSTS_PATH} failed: {exc}", file=sys.stderr)
        return EXIT_PROVIDER_ERROR
    print(json.dumps(body, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
