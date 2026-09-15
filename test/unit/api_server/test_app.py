"""Application-level tests for the serving API: metadata and error contracts.

Route behavior lives in the per-route modules; this module covers what
``create_app`` itself is responsible for.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from psycopg import sql

from manta_trading.api_server.app import create_app, make_configure_connection
from manta_trading.constants import API_SERVING_SESSION, DbSessionSettings
from manta_trading.version import package_version

_APP_MODULE = "manta_trading.api_server.app"
_DB_URL = "postgresql://user:pass@localhost:5432/nonexistent"


def test_openapi_version_comes_from_package_metadata() -> None:
    """Slice 186 D3 — one version in the repo, not a hardcoded literal.

    The pre-186 value was ``"0.1.0"`` while the distribution was at 0.7.3;
    asserting equality with ``package_version()`` is what keeps them married.
    """
    info = create_app().openapi()["info"]
    assert info["version"] == package_version()
    assert info["version"] != "0.1.0"


# --- Lifespan wiring (slice 186 D1, D9) -------------------------------------


class RecordingConnection:
    """Records the SET statements a pool ``configure`` hook issues, rendered
    to SQL text (the hook composes them with ``psycopg.sql`` since 262)."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.autocommit = False

    def execute(self, statement: str | sql.Composable) -> None:
        rendered = statement if isinstance(statement, str) else statement.as_string()
        self.statements.append(rendered)


def _emitted(session: DbSessionSettings) -> list[str]:
    conn = RecordingConnection()
    make_configure_connection(session)(conn)  # type: ignore[arg-type]
    return conn.statements


@pytest.fixture
def started_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Enter the real lifespan with every pool constructor patched out.

    Yields ``(app, pool_cls, minute_cls, daily_cls)`` so a test can inspect
    both what was stored on ``app.state`` and what each pool was built with.
    """

    def _start(**env: str) -> tuple[Any, Any, Any, Any]:
        monkeypatch.setenv("MT_TIMESCALE_DB_URL", _DB_URL)
        for key in ("MT_API_MAX_BARS_PER_REQUEST", "MT_API_STATEMENT_TIMEOUT"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        app = create_app()
        with (
            patch(f"{_APP_MODULE}.ConnectionPool") as pool_cls,
            patch(f"{_APP_MODULE}.TimescaleMinuteDataDB") as minute_cls,
            patch(f"{_APP_MODULE}.TimescaleDailyDataDB") as daily_cls,
        ):
            from fastapi.testclient import TestClient

            with TestClient(app):
                pass
        return app, pool_cls, minute_cls, daily_cls

    return _start


def test_lifespan_gives_all_three_pools_the_serving_session(started_app: Any) -> None:
    """D1's whole point: the bars path runs on the two class-owned pools, so
    configuring only ``app.state.db_pool`` would leave bars at 300s/512MB."""
    _app, pool_cls, minute_cls, daily_cls = started_app()

    assert _emitted(minute_cls.call_args.kwargs["session"]) == _emitted(
        API_SERVING_SESSION
    )
    assert (
        daily_cls.call_args.kwargs["session"] == minute_cls.call_args.kwargs["session"]
    )

    conn = RecordingConnection()
    pool_cls.call_args.kwargs["configure"](conn)
    assert "SET work_mem = '64MB'" in conn.statements
    assert "SET statement_timeout = '20s'" in conn.statements


def test_statement_timeout_override_reaches_the_pools(started_app: Any) -> None:
    """Proves the setting reaches the connection, not merely ``Settings``."""
    _app, pool_cls, minute_cls, daily_cls = started_app(MT_API_STATEMENT_TIMEOUT="5s")

    conn = RecordingConnection()
    pool_cls.call_args.kwargs["configure"](conn)
    assert "SET statement_timeout = '5s'" in conn.statements
    for cls in (minute_cls, daily_cls):
        assert cls.call_args.kwargs["session"].statement_timeout == "5s"
        # work_mem is not operator-settable (D9) and must not move with it.
        assert cls.call_args.kwargs["session"].work_mem == "64MB"


def test_policy_values_are_resolved_once_onto_app_state(started_app: Any) -> None:
    app, _pool_cls, _minute_cls, _daily_cls = started_app(
        MT_API_MAX_BARS_PER_REQUEST="1234", MT_API_STATEMENT_TIMEOUT="7s"
    )
    assert app.state.max_bars_per_request == 1234
    assert app.state.statement_timeout == "7s"


def test_no_bulk_literals_remain_in_the_api_module() -> None:
    """A literal 512MB/300s in app.py would silently reinstate the bulk budget
    on whichever pool it configured."""
    import inspect

    from manta_trading.api_server import app as app_module

    source = inspect.getsource(app_module)
    assert "512MB" not in source
    assert "300s" not in source


# --- Error-body contract (slice 186 D6) -------------------------------------


def _error_app() -> Any:
    """An app with two routes that raise the two HTTPException shapes."""
    from fastapi import HTTPException

    app = create_app()

    @app.get("/_test/not-found")
    async def _not_found() -> None:
        raise HTTPException(status_code=404, detail="Symbol 'ZZZZ' not found")

    @app.get("/_test/unprocessable")
    async def _unprocessable() -> None:
        raise HTTPException(status_code=422, detail="bad range")

    return app


@pytest.mark.parametrize(
    ("path", "status_code", "message"),
    [
        ("/_test/not-found", 404, "Symbol 'ZZZZ' not found"),
        ("/_test/unprocessable", 422, "bad range"),
    ],
)
def test_http_exceptions_all_render_as_error(
    path: str, status_code: int, message: str
) -> None:
    """D6 widened the handler past its 404-only special case; a 422 used to
    fall through to FastAPI's ``{"detail": ...}``."""
    from fastapi.testclient import TestClient

    response = TestClient(_error_app()).get(path)
    assert response.status_code == status_code
    assert response.json() == {"error": message}


def test_fastapi_validation_body_stays_native() -> None:
    """The one documented exception to D6, asserted deliberately: a future
    change must not silently flatten ``loc``/``msg`` into a string."""
    from fastapi.testclient import TestClient

    app = create_app()
    app.state.db_pool = MagicMock()
    app.state.minute_db = MagicMock()
    app.state.daily_db = MagicMock()
    app.state.max_bars_per_request = 75_000
    response = TestClient(app).get(
        "/api/v1/bars/SPY?granularity=bad&start=2024-01-01&end=2024-01-03"
    )
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body
    assert isinstance(body["detail"], list)
    assert "error" not in body


def test_unhandled_exception_body_is_sanitized() -> None:
    from fastapi.testclient import TestClient

    app = create_app()

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise RuntimeError("connection string postgresql://user:pass@host/db")

    response = TestClient(app, raise_server_exceptions=False).get("/_test/boom")
    assert response.status_code == 500
    assert response.json() == {"error": "internal server error"}


def test_missing_db_url_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """No silent fallback: the server refuses to start without a URL."""
    monkeypatch.setenv("MT_TIMESCALE_DB_URL", "")
    app = create_app()

    from fastapi.testclient import TestClient

    with (
        patch(f"{_APP_MODULE}.ConnectionPool", MagicMock()),
        pytest.raises(RuntimeError, match="MT_TIMESCALE_DB_URL"),
        TestClient(app),
    ):
        pass


# --- create_app(db_url=...) seam (slice 187 D9) ------------------------------

_SEAM_URL = "postgresql://user:pass@localhost:5432/seam_target"
"""A second obviously-fake URL, distinct from ``_DB_URL`` above.

Placeholder credentials matching that constant's convention — no connection is
ever opened with either (``ConnectionPool`` is patched out in every test that
uses them). Distinctness is the load-bearing property: the seam tests assert
which of the two URLs reached the pool, so two different *values* are required
and two different *credentials* are not.
"""


def _start_with(app: Any) -> Any:
    """Enter ``app``'s lifespan with the pool constructors patched out.

    Returns the patched ``ConnectionPool`` class so a test can read the conninfo
    the pool was actually built with — which is the only thing that proves which
    URL won.
    """
    from fastapi.testclient import TestClient

    with (
        patch(f"{_APP_MODULE}.ConnectionPool") as pool_cls,
        patch(f"{_APP_MODULE}.TimescaleMinuteDataDB") as minute_cls,
        patch(f"{_APP_MODULE}.TimescaleDailyDataDB") as daily_cls,
    ):
        with TestClient(app):
            pass
        return pool_cls, minute_cls, daily_cls


def test_db_url_argument_overrides_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam the load tier needs: an explicit URL beats the environment.

    Asserted against a *different* MT_TIMESCALE_DB_URL rather than an unset one,
    so a seam that silently ignored its argument would fail here.
    """
    monkeypatch.setenv("MT_TIMESCALE_DB_URL", _DB_URL)
    pool_cls, minute_cls, daily_cls = _start_with(create_app(db_url=_SEAM_URL))

    assert pool_cls.call_args.args[0] == _SEAM_URL
    # All three pools, not just app.state.db_pool — the bars path runs on the
    # two class-owned ones, and a load test pointing two of three at production
    # would be worse than no seam at all.
    assert minute_cls.call_args.args[0] == _SEAM_URL
    assert daily_cls.call_args.args[0] == _SEAM_URL


def test_no_argument_is_behaviour_identical_to_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_app()`` must read Settings exactly as it did before the seam."""
    monkeypatch.setenv("MT_TIMESCALE_DB_URL", _DB_URL)
    pool_cls, minute_cls, daily_cls = _start_with(create_app())

    assert pool_cls.call_args.args[0] == _DB_URL
    assert minute_cls.call_args.args[0] == _DB_URL
    assert daily_cls.call_args.args[0] == _DB_URL


def test_missing_db_url_still_fails_loudly_with_the_seam_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-URL path is unchanged: the seam added an override, not a fallback.

    Distinct from ``test_missing_db_url_fails_loudly`` above, which pins the
    same behavior for the pre-seam call shape. Both must hold — an empty
    ``db_url`` must not become a silent read of Settings *or* a silent start.
    """
    monkeypatch.setenv("MT_TIMESCALE_DB_URL", "")
    from fastapi.testclient import TestClient

    with (
        patch(f"{_APP_MODULE}.ConnectionPool", MagicMock()),
        pytest.raises(RuntimeError, match="MT_TIMESCALE_DB_URL"),
        TestClient(create_app(db_url=None)),
    ):
        pass


def test_explicit_url_starts_without_any_settings_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load tier's actual requirement (D9): the app starts from the seam
    alone, with MT_TIMESCALE_DB_URL unset, so no load-test line ever needs to
    read the production variable."""
    monkeypatch.setenv("MT_TIMESCALE_DB_URL", "")
    pool_cls, _minute_cls, _daily_cls = _start_with(create_app(db_url=_SEAM_URL))

    assert pool_cls.call_args.args[0] == _SEAM_URL


class TestTheCreditExecutorLifecycle:
    """The credit executor is owned by the app, not by the module (189 F001).

    It began as a module-level global created at import. That worked, but it
    was the one shared resource in this app with no lifecycle: invisible to
    ``create_app``/``lifespan``, shared between every app in a test session,
    and unreachable from the shutdown path. These tests pin the correction.
    """

    def test_lifespan_creates_it(self, started_app: Any) -> None:
        app, _pool_cls, _minute_cls, _daily_cls = started_app()

        assert app.state.credit_executor is not None

    def test_lifespan_shuts_it_down(self, started_app: Any) -> None:
        """Teardown reaches it, which is what a module-level global prevented.

        ``_shutdown`` is the flag ``ThreadPoolExecutor.shutdown`` sets; asserted
        rather than calling ``submit`` because a shut-down executor raises on
        submit and the point here is that teardown *ran*.
        """
        app, _pool_cls, _minute_cls, _daily_cls = started_app()

        assert app.state.credit_executor._shutdown is True

    def test_each_app_gets_its_own(self, started_app: Any) -> None:
        """One per app, so a test app and the production app never share
        threads — the same guarantee ``UniverseEdgeCache`` is given."""
        first, _pc, _mc, _dc = started_app()
        second, _pc2, _mc2, _dc2 = started_app()

        assert first.state.credit_executor is not second.state.credit_executor

    def test_it_is_bounded_and_small(self) -> None:
        """The ceiling is the whole point: however many credit requests
        arrive, they can occupy only these threads (D8)."""
        from manta_trading.api_server.routes.operations import (
            CREDIT_EXECUTOR_THREADS,
            make_credit_executor,
        )

        executor = make_credit_executor()
        try:
            assert executor._max_workers == CREDIT_EXECUTOR_THREADS
            assert CREDIT_EXECUTOR_THREADS < 32
        finally:
            executor.shutdown(wait=False)
