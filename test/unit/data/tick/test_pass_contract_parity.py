"""The tick pass contract against its Kalshi original (LLD 224 TD1, FR9).

The tick copy may differ from ``data/kalshi/collection_pass.py`` only where TD1
declares it (two more outcomes, phase names, no event sink or ``on_phase``, no
historical phase). This test imports both packages, which the import boundary
allows because it binds ``src``, not tests. Adding a field to either
``PhaseReport`` or ``PassResult`` makes it fail.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from manta_trading.data.kalshi import collection_pass as kalshi
from manta_trading.data.kalshi.sync_types import SyncOutcome
from manta_trading.data.tick import pass_contract as tick
from manta_trading.data.tick.pass_contract import TickOutcome, TickPassPhaseName

#: Annotation text differs only in the names TD1 declares as divergent.
_DIVERGENT_NAMES = {
    "SyncOutcome": "TickOutcome",
    "PassPhaseName": "TickPassPhaseName",
}
_TICK_ONLY = {TickOutcome.REFUSED, TickOutcome.IN_FLIGHT}
_NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _fields(cls: type) -> list[tuple[str, str]]:
    return [(f.name, str(f.type)) for f in dataclasses.fields(cls)]


def _normalised(fields: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out = []
    for name, annotation in fields:
        for kalshi_name, tick_name in _DIVERGENT_NAMES.items():
            annotation = annotation.replace(kalshi_name, tick_name)
        out.append((name, annotation))
    return out


@pytest.mark.parametrize("name", ["PhaseReport", "PassResult"])
def test_dataclass_fields_match_kalshi(name: str) -> None:
    assert _fields(getattr(tick, name)) == _normalised(_fields(getattr(kalshi, name)))


def test_outcome_members_differ_only_by_the_declared_two() -> None:
    assert set(TickOutcome) - set(SyncOutcome) == _TICK_ONLY
    shared = {o.name for o in TickOutcome if o not in _TICK_ONLY}
    assert shared == {o.name for o in SyncOutcome}


def test_shared_outcome_values_are_equal() -> None:
    for member in SyncOutcome:
        assert TickOutcome[member.name].value == member.value


def test_skipped_marker_matches() -> None:
    assert tick.SKIPPED == kalshi.SKIPPED


# -- abort-then-skip, driven through both runners on one report sequence -------


class _Run:
    """Just what either runner reads from its run object."""

    run_id = uuid4()

    def __init__(self) -> None:
        self.clock = lambda: _NOW
        self.client = _Client()


class _Client:
    mode = "test"

    class rate_limit:  # noqa: N801 - mimics the client attribute path
        requests_per_minute = 1


class _Sink:
    def emit(self, event: object) -> None:
        return None


def _phases(report_cls: Any, names: list[Any], outcomes: list[Any]) -> list[Any]:
    def make(name: Any, outcome: Any) -> Any:
        class Phase:
            async def run(self, run: object) -> Any:
                return report_cls(name, outcome, {}, 0)

        phase = Phase()
        phase.name = name  # type: ignore[attr-defined]
        return phase

    return [make(n, o) for n, o in zip(names, outcomes, strict=True)]


def _shape(reports: tuple[Any, ...]) -> list[str]:
    return [str(r.outcome) for r in reports]


SEQUENCES = [
    ["ok", "ok", "ok"],
    ["partial", "ok", "ok"],
    ["ok", "provider_abort", "ok"],
    ["storage_abort", "ok", "ok"],
    ["ok", "ok", "partial"],
]


@pytest.mark.parametrize("sequence", SEQUENCES)
def test_abort_then_skip_matches_kalshi(sequence: list[str]) -> None:
    run = _Run()
    kalshi_names = list(kalshi.PassPhaseName)[:3]
    tick_names = list(TickPassPhaseName)[:3]
    kalshi_pass = kalshi.CollectionPass(
        run,  # type: ignore[arg-type]
        _phases(kalshi.PhaseReport, kalshi_names, [SyncOutcome(o) for o in sequence]),
    )
    kalshi_pass._emit = lambda *a, **k: None  # type: ignore[method-assign]
    tick_pass = tick.TickPass(
        run,  # type: ignore[arg-type]
        _phases(tick.PhaseReport, tick_names, [TickOutcome(o) for o in sequence]),
    )
    kalshi_result = asyncio.run(kalshi_pass.run())
    tick_result = asyncio.run(tick_pass.run())
    assert _shape(tick_result.reports) == _shape(kalshi_result.reports)
    assert str(tick_result.outcome) == str(kalshi_result.outcome)


# -- precedence over every pair of outcomes ------------------------------------

_PRECEDENCE = [
    TickOutcome.STORAGE_ABORT,
    TickOutcome.PROVIDER_ABORT,
    TickOutcome.PARTIAL,
    TickOutcome.REFUSED,
    TickOutcome.IN_FLIGHT,
    TickOutcome.OK,
]


def _report(outcome: Any) -> tick.PhaseReport:
    return tick.PhaseReport(TickPassPhaseName.PURCHASE, outcome, {}, 0)


def test_precedence_list_covers_every_outcome() -> None:
    assert set(_PRECEDENCE) == set(TickOutcome)


@pytest.mark.parametrize(
    ("worse", "better"), list(itertools.combinations(_PRECEDENCE, 2))
)
def test_classify_pass_takes_the_worse_of_any_pair(
    worse: TickOutcome, better: TickOutcome
) -> None:
    assert tick.classify_pass([_report(better), _report(worse)]) is worse
    assert tick.classify_pass([_report(worse), _report(better)]) is worse


def test_classify_pass_ignores_skipped_and_defaults_to_ok() -> None:
    assert tick.classify_pass([_report(tick.SKIPPED)]) is TickOutcome.OK
    assert tick.classify_pass([]) is TickOutcome.OK


def test_only_aborts_skip_later_phases() -> None:
    for outcome in (TickOutcome.REFUSED, TickOutcome.IN_FLIGHT, TickOutcome.PARTIAL):
        run = _Run()
        phases = _phases(
            tick.PhaseReport, list(TickPassPhaseName)[:2], [outcome, TickOutcome.OK]
        )
        result = asyncio.run(tick.TickPass(run, phases).run())  # type: ignore[arg-type]
        assert _shape(result.reports) == [str(outcome), "ok"]


def test_pass_result_json_shape() -> None:
    run = _Run()
    phases = _phases(tick.PhaseReport, [TickPassPhaseName.RECONCILE], [TickOutcome.OK])
    payload = asyncio.run(tick.TickPass(run, phases).run()).to_dict()  # type: ignore[arg-type]
    assert set(payload) == {
        "run_id",
        "started_at",
        "phases",
        "outcome",
        "duration_ms",
    }
    assert payload["phases"][0]["name"] == "reconcile"
