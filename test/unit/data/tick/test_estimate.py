"""Estimate core: bundle verdicts, the edge refusal, the day tally (slice 220).

Figures come from ``tick_support.metadata_responses`` through a real
``DatabentoTickProvider`` over a fake ``Historical`` whose ``batch`` and
``timeseries`` raise on access — so every test here is also the
"no billable surface touched" backstop.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from tick_support.fake_historical import FakeApi, FakeHistorical
from tick_support.metadata_responses import (
    REQUEST_END,
    REQUEST_START,
    metadata_api,
    symbology_api,
)

from manta_trading.data.tick.constants import (
    CME_DATASET,
    ESTIMATE_SCHEMAS,
    DatasetCondition,
    SType,
    TickSchema,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.estimate import (
    CeilingVerdict,
    EstimateRefusedError,
    build_estimate,
)
from manta_trading.data.tick.provider import TickRequest

REQUEST = TickRequest(
    dataset=CME_DATASET,
    symbols=("ES.c.0",),
    stype_in=SType.CONTINUOUS,
    schema=TickSchema.TRADES,
    start=REQUEST_START,
    end=REQUEST_END,
)
# From metadata_responses.FIGURES: trades 1.25, tbbo 2.5, mbp-1 40, definition 0.75.
TRADES_BUNDLE = Decimal("2.00")
TBBO_BUNDLE = Decimal("3.25")


def _provider(metadata: FakeApi | None = None) -> DatabentoTickProvider:
    """``batch`` and ``timeseries`` are not supplied: any access raises."""
    client = FakeHistorical(
        metadata=metadata or metadata_api(), symbology=symbology_api()
    )
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(599)))
    return DatabentoTickProvider(client, http)  # type: ignore[arg-type]


def _verdicts(ceiling: Decimal | None) -> dict[TickSchema, CeilingVerdict]:
    report = build_estimate(_provider(), REQUEST, ceiling)
    return {row.schema: row.verdict for row in report.rows}


def test_rows_follow_estimate_schemas_with_figures() -> None:
    report = build_estimate(_provider(), REQUEST, None)
    assert [row.schema for row in report.rows] == list(ESTIMATE_SCHEMAS)
    trades = report.rows[0]
    assert (trades.record_count, trades.billable_bytes) == (2_000_000, 96_000_000)
    assert trades.cost_usd == Decimal("1.25")
    assert trades.bundle_cost_usd == TRADES_BUNDLE
    assert report.rows[1].bundle_cost_usd == TBBO_BUNDLE


def test_unset_ceiling() -> None:
    assert _verdicts(None) == {
        TickSchema.TRADES: CeilingVerdict.NO_CEILING,
        TickSchema.TBBO: CeilingVerdict.NO_CEILING,
        TickSchema.MBP_1: CeilingVerdict.NOT_PURCHASABLE,
        TickSchema.DEFINITION: CeilingVerdict.BOUGHT_WITH_EACH_TIER,
    }
    assert "MT_TICK_SPEND_CEILING_USD unset" in CeilingVerdict.NO_CEILING


@pytest.mark.parametrize("ceiling", [Decimal("0.01"), Decimal("2.75"), Decimal("1000")])
def test_mbp_1_is_never_purchasable(ceiling: Decimal) -> None:
    verdicts = _verdicts(ceiling)
    assert verdicts[TickSchema.MBP_1] is CeilingVerdict.NOT_PURCHASABLE
    assert verdicts[TickSchema.DEFINITION] is CeilingVerdict.BOUGHT_WITH_EACH_TIER


def test_ceiling_between_bundles_splits_the_tiers() -> None:
    verdicts = _verdicts(Decimal("2.75"))
    assert verdicts[TickSchema.TRADES] is CeilingVerdict.WITHIN
    assert verdicts[TickSchema.TBBO] is CeilingVerdict.OVER


def test_tier_alone_within_but_bundle_over_is_over() -> None:
    """tbbo alone costs 2.50 — within 3.00 — but its bundle is 3.25."""
    assert _verdicts(Decimal("3.00"))[TickSchema.TBBO] is CeilingVerdict.OVER


def test_ceiling_equal_to_bundle_is_within() -> None:
    assert _verdicts(TRADES_BUNDLE)[TickSchema.TRADES] is CeilingVerdict.WITHIN


def test_end_past_the_edge_is_refused_naming_it() -> None:
    late = TickRequest(
        dataset=CME_DATASET,
        symbols=("ES.c.0",),
        stype_in=SType.CONTINUOUS,
        schema=TickSchema.TRADES,
        start=date(2025, 9, 20),
        end=date(2025, 9, 27),
    )
    metadata = metadata_api()
    with pytest.raises(EstimateRefusedError, match="2025-09-26T00:00:00"):
        build_estimate(_provider(metadata), late, None)
    assert [name for name, _ in metadata.calls] == ["get_dataset_range"]


def test_end_at_the_edge_is_allowed() -> None:
    at_edge = TickRequest(
        dataset=CME_DATASET,
        symbols=("ES.c.0",),
        stype_in=SType.CONTINUOUS,
        schema=TickSchema.TRADES,
        start=date(2025, 9, 22),
        end=date(2025, 9, 26),
    )
    build_estimate(_provider(), at_edge, None)


def test_condition_tally_counts_each_day_once() -> None:
    metadata = metadata_api()
    labels = ["available", "degraded", "available", "pending", "missing"]
    metadata.responses["get_dataset_condition"] = [
        {"date": f"2025-01-{6 + n:02d}", "condition": c, "last_modified_date": None}
        for n, c in enumerate(labels)
    ]
    report = build_estimate(_provider(metadata), REQUEST, None)
    assert report.conditions == {
        DatasetCondition.AVAILABLE: 2,
        DatasetCondition.DEGRADED: 1,
        DatasetCondition.PENDING: 1,
        DatasetCondition.MISSING: 1,
    }
    assert sum(report.conditions.values()) == (REQUEST_END - REQUEST_START).days


def test_each_metadata_call_happens_once_per_schema() -> None:
    metadata = metadata_api()
    build_estimate(_provider(metadata), REQUEST, None)
    names = [name for name, _ in metadata.calls]
    assert names.count("get_dataset_range") == 1
    assert names.count("get_dataset_condition") == 1
    for method in ("get_record_count", "get_billable_size", "get_cost"):
        sent = [kwargs["schema"] for kwargs in metadata.calls_to(method)]
        assert sent == [s.value for s in ESTIMATE_SCHEMAS]


def test_to_dict_carries_the_ceiling_fields() -> None:
    payload = build_estimate(_provider(), REQUEST, Decimal("2.75")).to_dict()
    assert payload["ceiling_usd"] == "2.75"
    rows = payload["schemas"]
    assert isinstance(rows, list)
    assert rows[0]["bundle_cost_usd"] == "2.00"
    assert rows[0]["ceiling_verdict"] == "within"
    assert rows[1]["ceiling_verdict"] == "over"
    assert rows[2]["bundle_cost_usd"] is None
    assert payload["end_exclusive"] == "2025-01-11"
