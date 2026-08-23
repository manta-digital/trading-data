"""``mt serve`` — start the Data Serving API server."""

from __future__ import annotations

import typer
import uvicorn


def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8100, help="Bind port."),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Auto-reload on code changes. Dev mode only.",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        help=(
            "Number of uvicorn worker processes (default 1). "
            "In production, mt serve runs under the mt-serve systemd unit."
        ),
    ),
) -> None:
    """Start the Data Serving API server."""
    uvicorn.run(
        "manta_trading.api_server.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        workers=workers,
    )
