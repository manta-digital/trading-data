"""Single source for the installed distribution version (slice 186 D3).

A leaf module by design: the CLI's ``--version`` callback and the API's
OpenAPI ``info.version`` both read from here, so the repo reports one version
in one way. Imports nothing from ``cli`` or ``api_server``.
"""

from __future__ import annotations

import importlib.metadata

from manta_trading.constants import DISTRIBUTION_NAME
from manta_trading.logging import get_logger

logger = get_logger(__name__)


def package_version() -> str:
    """Return the installed distribution version, or ``"dev"``.

    ``"dev"`` is an obviously-placeholder value for a source checkout with no
    installed metadata, and it is logged when it happens — not a silent
    fallback that lets an unversioned build pass for a released one.
    """
    try:
        return importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        logger.warning(
            "No installed distribution metadata found for %r; "
            "reporting version as 'dev'.",
            DISTRIBUTION_NAME,
        )
        return "dev"
