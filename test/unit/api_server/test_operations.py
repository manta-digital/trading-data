"""Unit tests for ``GET /api/v1/overview`` and ``GET /api/v1/credits`` (189).

No database and no network: ``gather_db_facts`` and ``fetch_credit_usage`` are
patched, which is the whole point of 922's split between reading and deriving.

The assertions that matter most here are the ones a looser test would let
through:

- **D4 is pinned by inspecting the argument, not the body.** Asserting
  "``abandoned`` is absent" passes even with a real ``pid_is_alive``, because
  the models omit the field either way. What actually pins the decision is the
  ``pid_alive`` keyword the route hands to ``build_overview``.
- **Redaction is proven on a real exception**, not assumed, using a message
  shaped like the one the fetch actually raises: the API key rides in the
  request URL's query string.
- **SC2 is pinned by a test rather than a grep.** A grep decays the moment
  someone adds a line; an assertion travels with the code.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.cli.overview_types import (
    CREDITS_NO_KEY,
    OverviewFacts,
    SourceFreshness,
)
from manta_trading.data.acquisition.pass_runs import PassKind

NOW = datetime(2026, 9, 13, 14, 22, 3, tzinfo=UTC)
HOST = "manta9000"
ROUTE_MODULE = "manta_trading.api_server.routes.operations"


def _facts() -> OverviewFacts:
    """Facts as ``gather_db_facts`` would return them: DB fields only."""
    facts = OverviewFacts(NOW, HOST, minute_firing_days=(0, 3))
    facts.open_runs = {kind: [] for kind in PassKind}
    facts.latest_ended = {kind: None for kind in PassKind}
    facts.sources = [SourceFreshness("minute bars", NOW - timedelta(hours=18))]
    return facts


@pytest.fixture
def app() -> FastAPI:
    """An app with lifespan side-stepped: no pool, no real Settings."""
    built = create_app()
    built.state.db_pool = MagicMock(name="sentinel_pool")
    built.state.settings = SimpleNamespace(
        minute_firing_days=(0, 3), eodhd_api_key="secret-key-value"
    )
    built.dependency_overrides[get_db] = lambda: MagicMock(name="sentinel_conn")
    return built


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestOverviewRoute:
    def test_it_returns_the_overview_shape(self, client, monkeypatch) -> None:
        monkeypatch.setattr(
            f"{ROUTE_MODULE}.gather_db_facts", lambda _conn, _settings: _facts()
        )

        response = client.get("/api/v1/overview")

        assert response.status_code == 200
        body = response.json()
        assert body["now"] == "2026-09-13T14:22:03Z"
        assert {line["pass"] for line in body["passes"]} == {
            kind.value for kind in PassKind
        }
        assert body["sources"] == [
            {"name": "minute bars", "newest": "2026-09-12T20:22:03Z"}
        ]
        assert "health" in body
        assert "universe" in body

    def test_it_serves_no_credits(self, client, monkeypatch) -> None:
        """SC7: the credit line is a different route's job (D2)."""
        monkeypatch.setattr(
            f"{ROUTE_MODULE}.gather_db_facts", lambda _conn, _settings: _facts()
        )

        assert "credits" not in client.get("/api/v1/overview").text

    def test_it_forces_pid_alive_true(self, client, monkeypatch) -> None:
        """D4, pinned where it is actually decided.

        The route must never judge abandonment: a pid recorded on another host
        says nothing about a process here. Inspecting the keyword is the only
        assertion that fails when someone passes a real ``pid_is_alive``,
        because the response body looks identical either way.
        """
        monkeypatch.setattr(
            f"{ROUTE_MODULE}.gather_db_facts", lambda _conn, _settings: _facts()
        )
        seen: dict[str, object] = {}
        real_build = __import__(
            ROUTE_MODULE, fromlist=["build_overview"]
        ).build_overview

        def _spy(facts, *, pid_alive):
            seen["pid_alive"] = pid_alive
            return real_build(facts, pid_alive=pid_alive)

        monkeypatch.setattr(f"{ROUTE_MODULE}.build_overview", _spy)

        assert client.get("/api/v1/overview").status_code == 200

        pid_alive = seen["pid_alive"]
        assert callable(pid_alive)
        # True for any pid, including ones no process could own.
        for pid in (1, 38214, 2**31 - 1):
            assert pid_alive(pid) is True

    def test_it_has_no_parameters(self, app) -> None:
        """D6: no query or path parameters, so no 404 and no 422."""
        spec = app.openapi()["paths"]["/api/v1/overview"]["get"]

        assert spec.get("parameters", []) == []
        assert sorted(spec["responses"]) == ["200", "504"]


class TestCreditsRoute:
    """All three shapes are 200 (D6/SC6)."""

    def test_usage_present(self, client, monkeypatch) -> None:
        monkeypatch.setattr(
            f"{ROUTE_MODULE}.fetch_credit_usage",
            lambda _key: CreditUsage(98412, 100000, 0),
        )

        response = client.get("/api/v1/credits")

        assert response.status_code == 200
        assert response.json() == {
            "credits": {
                "used": 98412,
                "daily_limit": 100000,
                "extra": 0,
                "remaining": 1588,
            },
            "error": None,
        }

    def test_fetch_failure_is_reported_not_raised(self, client, monkeypatch) -> None:
        def _raise(_key):
            raise RuntimeError("connection timed out")

        monkeypatch.setattr(f"{ROUTE_MODULE}.fetch_credit_usage", _raise)

        response = client.get("/api/v1/credits")

        assert response.status_code == 200
        body = response.json()
        assert body["credits"] is None
        assert body["error"] == "unavailable (connection timed out)"

    def test_no_key_configured(self, app, monkeypatch) -> None:
        app.state.settings = SimpleNamespace(
            minute_firing_days=(0, 3), eodhd_api_key=None
        )

        def _must_not_be_called(_key):  # pragma: no cover - asserted below
            raise AssertionError("the route fetched with no key configured")

        monkeypatch.setattr(f"{ROUTE_MODULE}.fetch_credit_usage", _must_not_be_called)

        response = TestClient(app).get("/api/v1/credits")

        assert response.status_code == 200
        assert response.json() == {"credits": None, "error": CREDITS_NO_KEY}

    def test_the_api_token_never_reaches_the_body(self, client, monkeypatch) -> None:
        """SC6: redaction proven on the shape the fetch really raises.

        ``fetch_credit_usage`` calls a URL carrying ``api_token=`` in its query
        string, and httpx puts that URL in the exception text. A test that
        raised a bare message would prove nothing about the case that matters.
        """
        token = "6890abcdef1234567890"

        def _raise(_key):
            raise RuntimeError(
                "Client error '401 Unauthorized' for url "
                f"'https://eodhd.com/api/user?api_token={token}&fmt=json'"
            )

        monkeypatch.setattr(f"{ROUTE_MODULE}.fetch_credit_usage", _raise)

        response = client.get("/api/v1/credits")

        assert response.status_code == 200
        assert token not in response.text
        assert response.json()["credits"] is None

    def test_it_declares_no_gateway_timeout(self, app) -> None:
        """D8: the route issues no statement and takes no parameters, so a 504
        and its "narrow the requested range" remedy would both be false in the
        published schema. Declared nowhere, asserted here so it is not added
        later for symmetry with the sibling route."""
        spec = app.openapi()["paths"]["/api/v1/credits"]["get"]

        assert sorted(spec["responses"]) == ["200"]
        assert "504" not in spec["responses"]


class TestNoSecondDerivation:
    """SC2: the route reuses 922's derivation rather than repeating it.

    Pinned as a test rather than a grep. A grep is a command someone has to
    remember to run and decays the moment a line moves; this assertion travels
    with the code and fails in the ordinary test run.
    """

    _FORBIDDEN = ("next_firing_at", "schedule_for", "pid_is_alive", "PassRunRepository")

    def test_it_imports_build_overview(self) -> None:
        module = __import__(ROUTE_MODULE, fromlist=["build_overview"])

        assert hasattr(module, "build_overview")

    def test_it_reaches_for_none_of_the_derivation_symbols(self) -> None:
        """The four symbols a second derivation would have to name.

        Checked against the module's *code* rather than its text. D4 requires
        a comment naming ``pid_is_alive`` to say why the route must not use
        it, so a raw substring search over the source would fail on the very
        comment the design asks for — and, worse, would pressure a later
        reader into deleting the explanation to make a test pass.
        """
        module = __import__(ROUTE_MODULE, fromlist=["build_overview"])
        code = _code_without_comments(inspect.getsource(module))

        for symbol in self._FORBIDDEN:
            assert symbol not in code, (
                f"{symbol} appears in the route module's code: the overview is "
                "derived once, by build_overview (189 D3/SC2)"
            )

    def test_the_comment_explaining_d4_is_still_there(self) -> None:
        """The flip side: the reason must survive.

        ``pid_alive=lambda _pid: True`` is the kind of line a later reader
        simplifies away without the comment saying what it is for.
        """
        source = inspect.getsource(
            __import__(ROUTE_MODULE, fromlist=["build_overview"])
        )

        assert "pid_is_alive" in source
        assert "D4" in source

    def test_the_module_issues_no_sql(self) -> None:
        code = _code_without_comments(
            inspect.getsource(__import__(ROUTE_MODULE, fromlist=["build_overview"]))
        )

        for token in ("SELECT ", "FROM ", "execute("):
            assert token not in code


def _code_without_comments(source: str) -> str:
    """Source with comments and docstrings removed, leaving executable code.

    Uses the tokenizer rather than a regex so a ``#`` inside a string literal
    is not mistaken for a comment.
    """
    import io
    import tokenize

    kept: list[str] = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        # A string in statement position is a docstring, not a value.
        if token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.DEDENT,
        ):
            previous = token.type
            continue
        if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT):
            kept.append(token.string)
        previous = token.type
    return " ".join(kept)
