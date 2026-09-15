"""The committed schema's promises about the operations routes (189 Task 5.3).

``test_openapi_artifact.py`` asserts the artifact matches the app. These
assertions are about what the artifact *says*, and each one guards a decision
that would otherwise be a comment nobody checks:

- both routes publish a **200 schema** (SC9) — the trap 188 recorded, where
  ``response_class=Response`` suppresses it and leaves a UI generator with no
  type to import;
- ``504`` is declared on ``/api/v1/overview`` and **not** on
  ``/api/v1/credits`` (SC11/D8);
- the ``pass`` and ``outcome`` token sets equal the enums exactly (SC8), read
  from the enums rather than listed here.

Read from the committed artifact, not from the live app: the artifact is what
trading-ui and slice 190 consume, and a promise that holds only in memory is
not the one that ships.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome

ARTIFACT_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi.json"
)
OVERVIEW = "/api/v1/overview"
CREDITS = "/api/v1/credits"


def _artifact() -> dict[str, Any]:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _get(path: str) -> dict[str, Any]:
    return _artifact()["paths"][path]["get"]  # type: ignore[no-any-return]


def _resolve(ref: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``#/components/schemas/Name`` reference."""
    assert ref.startswith("#/"), ref
    node: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node  # type: ignore[no-any-return]


class TestBothRoutesArePublished:
    def test_they_are_in_the_committed_artifact(self) -> None:
        paths = _artifact()["paths"]

        assert OVERVIEW in paths
        assert CREDITS in paths

    def test_both_publish_a_200_schema(self) -> None:
        """SC9. Without this, a client has a route and no type for its body —
        the gap 188 left behind ``response_class=Response`` and handed to 190.
        """
        for path in (OVERVIEW, CREDITS):
            content = _get(path)["responses"]["200"]["content"]["application/json"]

            assert "schema" in content, f"{path} publishes no 200 schema"
            assert content["schema"].get("$ref"), (
                f"{path}'s 200 schema is inline, not a named component"
            )


class TestTheGatewayTimeoutDeclaration:
    """SC11/D8: declared where it can happen, absent where it cannot."""

    def test_overview_declares_504(self) -> None:
        assert "504" in _get(OVERVIEW)["responses"]

    def test_credits_does_not_declare_504(self) -> None:
        """The route issues no statement and takes no parameters, so both the
        status and the shared constant's "narrow the requested range" remedy
        would be false in the schema trading-ui and slice 190 consume."""
        responses = _get(CREDITS)["responses"]

        assert "504" not in responses, (
            "/api/v1/credits declares a 504 it cannot emit (189 D8)"
        )
        assert sorted(responses) == ["200"]

    def test_neither_route_declares_404_or_422(self) -> None:
        """D6: no path or query parameters, so neither status is reachable."""
        for path in (OVERVIEW, CREDITS):
            responses = _get(path)["responses"]

            assert "404" not in responses
            assert "422" not in responses
            assert _get(path).get("parameters", []) == []


class TestTheEnumTokenSets:
    """SC8: the published tokens are the enums', not a re-spelling.

    Compared against the enums themselves, so adding a member fails here until
    the artifact is regenerated — which is the point. A test listing the
    tokens would keep passing on exactly the day the contract changed.
    """

    def test_pass_tokens_equal_pass_kind(self) -> None:
        spec = _artifact()
        schema = _resolve(
            spec["components"]["schemas"]["PassLineRecord"]["properties"]["pass"][
                "$ref"
            ],
            spec,
        )

        assert set(schema["enum"]) == {kind.value for kind in PassKind}

    def test_outcome_tokens_equal_pass_run_outcome(self) -> None:
        spec = _artifact()
        schema = _resolve(
            spec["components"]["schemas"]["LastRunRecord"]["properties"]["outcome"][
                "$ref"
            ],
            spec,
        )

        assert set(schema["enum"]) == {
            outcome.value for outcome in PassRunOutcome
        }


class TestTheDeliberateOmissions:
    """D4/D2 as published: what the schema must not offer a client."""

    def test_running_record_declares_no_abandoned(self) -> None:
        properties = _artifact()["components"]["schemas"]["RunningRecord"][
            "properties"
        ]

        assert "abandoned" not in properties
        assert set(properties) == {
            "phase",
            "done",
            "total",
            "since",
            "progress_at",
            "hostname",
            "pid",
        }

    def test_the_overview_response_declares_no_credits(self) -> None:
        properties = _artifact()["components"]["schemas"]["OverviewResponse"][
            "properties"
        ]

        assert "credits" not in properties
        assert set(properties) == {"now", "passes", "sources", "health", "universe"}

    def test_the_wire_key_is_pass(self) -> None:
        """``pass_`` in the schema would be a contract break invisible to any
        test that only read the Python model."""
        properties = _artifact()["components"]["schemas"]["PassLineRecord"][
            "properties"
        ]

        assert "pass" in properties
        assert "pass_" not in properties
