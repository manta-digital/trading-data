"""Eligible-value SQL renderer: text built from ``OffTape`` (slice 171)."""

from __future__ import annotations

import pytest

from manta_trading.constants import OFF_TAPE_PRICE_BITS, OffTape
from manta_trading.market.schema import eligible_sql


def _terms(prefix: str) -> str:
    mask = f"COALESCE({prefix}off_tape, 0)"
    return ", ".join(
        f"CASE WHEN {mask} & {int(bit)} = 0 THEN {prefix}{name} END"
        for name, bit in OFF_TAPE_PRICE_BITS.items()
    )


@pytest.mark.parametrize(("alias", "prefix"), [(None, ""), ("m", "m.")])
def test_bounds_render_from_bits(alias: str | None, prefix: str) -> None:
    assert eligible_sql.eligible_low(alias) == f"LEAST({_terms(prefix)})"
    assert eligible_sql.eligible_high(alias) == f"GREATEST({_terms(prefix)})"


@pytest.mark.parametrize("column", list(OFF_TAPE_PRICE_BITS))
def test_value_clamps_between_bounds(column: str) -> None:
    lo, hi = eligible_sql.eligible_low("m"), eligible_sql.eligible_high("m")
    assert (
        eligible_sql.eligible_value(column, "m")
        == f"LEAST(GREATEST(m.{column}, {lo}), {hi})"
    )


def test_predicate_excludes_all_marked() -> None:
    assert eligible_sql.has_eligible_price() == (
        f"COALESCE(off_tape, 0) <> {int(OffTape.ALL)}"
    )


def test_rejects_non_price_column() -> None:
    with pytest.raises(ValueError, match="not a price column"):
        eligible_sql.eligible_value("volume")
