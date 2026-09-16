"""Integration: the documented `curl` examples still return the documented shape.

Slice 190 SC8. Every example block in ``docs/api/reference.md`` and
``docs/api/agents.md`` was executed against production and pasted with a
capture date. This file asserts the **shape** of those responses — status code,
top-level keys, field types — and never their values (190 D6): a price moves,
a `count` differs, and `now` is stale the instant it is written. What must not
change without a corresponding edit to the documents is the structure.

The gate (``scripts/check_api_docs.py``) checks the documents against the
committed schema. It cannot check that an example's pasted output was really
produced by the command above it, and it cannot notice when a response grows a
key. That is this file's job.

Every database here is one the fixture created — a UUID-named throwaway from
``ephemeral_db``. The production URL is never read; ``create_app(db_url=…)`` is
the 187 D9 seam that makes that guarantee structural rather than careful.

An empty database is enough for a shape assertion and is the point: these
routes must answer with their documented envelope when they have nothing to
report, which is exactly the case §2.4 of the reference describes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPO_ROOT / "docs" / "api" / "reference.md"
AGENTS = REPO_ROOT / "docs" / "api" / "agents.md"

CAPTURE_DATE = re.compile(r"\*Captured (\d{4}-\d{2}-\d{2})")
CURL_BLOCK = re.compile(r"```sh\n(curl [^`]*?)\n```", re.DOTALL)


def _client(db_url: str) -> TestClient:
    """A TestClient over an app pointed at one throwaway database."""
    return TestClient(create_app(db_url=db_url))


@pytest.fixture
def kalshi_documented_db(migrated_db: str) -> str:
    """A throwaway database carrying both tracks the Kalshi routes need.

    ``migrated_db`` applies the minute track the app itself opens against;
    the Kalshi catalog routes read ``kalshi.*``, which lives in its own track.
    Production carries both, so a shape assertion needs both too. Still a
    database this fixture's own ``ephemeral_db`` created.
    """
    from kalshi_support.schema import apply_kalshi_track

    return apply_kalshi_track(migrated_db)


class TestDocumentedEnvelopes:
    """Each route's documented top-level keys and their types.

    The expected key sets are written out rather than derived from the schema:
    deriving them would assert only that the schema matches itself, and the
    documents are what this test exists to hold in place.
    """

    def test_health_shape(self, migrated_db: str) -> None:
        with _client(migrated_db) as client:
            response = client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"status", "db", "coverage"}
        assert all(isinstance(value, str) for value in body.values())

    def test_symbols_shape(self, migrated_db: str) -> None:
        with _client(migrated_db) as client:
            response = client.get("/api/v1/symbols?search=SPY")

        assert response.status_code == 200
        body = response.json()
        # ``count`` was missing from the reference's field table and example
        # until this assertion caught it (SC8 doing its job).
        assert set(body) == {"symbols", "count"}
        assert isinstance(body["symbols"], list)
        assert isinstance(body["count"], int)

    def test_status_shape(self, migrated_db: str) -> None:
        """The documented envelope, including the `summary`/`count` split that
        §3.5 of the reference warns about."""
        with _client(migrated_db) as client:
            response = client.get("/api/v1/status")

        assert response.status_code == 200
        body = response.json()
        assert {"scope", "count", "rows", "summary", "coverage"} <= set(body)
        assert isinstance(body["count"], int)
        assert isinstance(body["rows"], list)
        assert isinstance(body["summary"], dict)
        assert set(body["coverage"]) == {"is_stale", "verdicts"}

    def test_status_coverage_verdict_shape(self, migrated_db: str) -> None:
        """`lag_seconds` is a float or null — never coerced to 0.0 (§3.5)."""
        with _client(migrated_db) as client:
            response = client.get("/api/v1/status")

        for verdict in response.json()["coverage"]["verdicts"]:
            assert set(verdict) == {
                "view_name",
                "is_fresh",
                "signals",
                "lag_seconds",
                "threshold_seconds",
                "detail",
            }
            assert isinstance(verdict["is_fresh"], bool)
            assert verdict["lag_seconds"] is None or isinstance(
                verdict["lag_seconds"], float
            )

    def test_overview_shape(self, migrated_db: str) -> None:
        with _client(migrated_db) as client:
            response = client.get("/api/v1/overview")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"now", "passes", "sources", "health", "universe"}
        assert isinstance(body["passes"], list)
        assert set(body["health"]) == {"verdict", "at"}

    def test_overview_pass_is_serialized_by_alias(self, migrated_db: str) -> None:
        """The wire name is ``pass``, not ``pass_`` (§5.3).

        Documented because a Python consumer cannot use it as an attribute
        name, so a client generator has to be told.
        """
        with _client(migrated_db) as client:
            body = client.get("/api/v1/overview").json()

        for line in body["passes"]:
            assert "pass" in line
            assert "pass_" not in line
            assert {"pass", "cadence", "running", "last_run", "next_firing"} <= set(
                line
            )

    def test_kalshi_categories_shape(self, kalshi_documented_db: str) -> None:
        with _client(kalshi_documented_db) as client:
            response = client.get("/api/v1/kalshi/categories")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"count", "categories"}
        assert isinstance(body["count"], int)
        assert isinstance(body["categories"], list)


class TestDocumentedErrorShapes:
    """Both `422` bodies, and the `404`. §2.7 of the reference turns on these
    being different, so a change to either is a documentation change."""

    def test_unknown_symbol_is_404_with_an_error_key(self, migrated_db: str) -> None:
        with _client(migrated_db) as client:
            response = client.get(
                "/api/v1/bars/NOSUCHSYM?granularity=1d&start=2024-06-10&end=2024-06-14"
            )

        assert response.status_code == 404
        assert set(response.json()) == {"error"}

    def test_fastapi_validation_422_uses_the_declared_shape(
        self, migrated_db: str
    ) -> None:
        """An unknown enum token is rejected before any handler runs, so it
        keeps FastAPI's ``{"detail": [...]}`` body."""
        with _client(migrated_db) as client:
            response = client.get(
                "/api/v1/bars/SPY?granularity=2d&start=2024-06-10&end=2024-06-14"
            )

        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"detail"}
        assert isinstance(body["detail"], list)
        assert {"loc", "msg", "type"} <= set(body["detail"][0])

    def test_hand_written_422_uses_the_other_shape(self, migrated_db: str) -> None:
        """A reversed range is refused inside the route, which sends
        ``{"error": "…"}`` — the shape the schema does not declare."""
        with _client(migrated_db) as client:
            response = client.get(
                "/api/v1/bars/SPY?granularity=1d&start=2024-06-14&end=2024-06-10"
            )

        assert response.status_code == 422
        assert set(response.json()) == {"error"}

    def test_the_two_422_shapes_differ(self, migrated_db: str) -> None:
        """The trap itself, asserted directly: same route, same status, two
        incompatible bodies. If this ever fails, §2.7 is wrong."""
        with _client(migrated_db) as client:
            declared = client.get(
                "/api/v1/bars/SPY?granularity=2d&start=2024-06-10&end=2024-06-14"
            ).json()
            hand_written = client.get(
                "/api/v1/bars/SPY?granularity=1d&start=2024-06-14&end=2024-06-10"
            ).json()

        assert set(declared) != set(hand_written)
        assert "detail" in declared
        assert "error" in hand_written


class TestExampleBlocksAreDated:
    """D6's staleness policy: an example without a capture date cannot be
    judged stale, so the date is part of the example."""

    def test_the_reference_carries_executed_examples(self) -> None:
        """``reference.md`` is the document that pastes real output (D6).

        ``agents.md`` deliberately carries none: it is capability-indexed and
        points at the reference for shapes, so requiring examples there would
        assert a structure the design does not have.
        """
        assert CURL_BLOCK.search(REFERENCE.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("document", [REFERENCE, AGENTS], ids=lambda p: p.name)
    def test_every_curl_block_is_followed_by_a_capture_date(
        self, document: Path
    ) -> None:
        text = document.read_text(encoding="utf-8")
        undated: list[str] = []
        blocks = list(CURL_BLOCK.finditer(text))
        for block in blocks:
            # The date may follow the command's own output block, so look
            # ahead past it rather than at the next line only.
            following = text[block.end() : block.end() + 2000]
            if not CAPTURE_DATE.search(following):
                undated.append(block.group(1).splitlines()[0])
        assert not undated, (
            f"{document.name}: curl examples with no capture date: {undated}"
        )

    def test_capture_dates_are_plausible(self) -> None:
        """A date typed as a placeholder (1970, 2099) is not a capture."""
        for document in (REFERENCE, AGENTS):
            for match in CAPTURE_DATE.finditer(document.read_text(encoding="utf-8")):
                year = int(match.group(1)[:4])
                assert 2025 <= year <= 2030, (
                    f"{document.name}: implausible capture date {match.group(1)}"
                )
