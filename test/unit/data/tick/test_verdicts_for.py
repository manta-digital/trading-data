"""``verdicts_for``: the one family-to-reader choice (slice 306 D3)."""

from __future__ import annotations

import pytest

from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import session_verdicts
from manta_trading.data.tick.tick_status_scope import verdicts_for


@pytest.mark.parametrize("family", [SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M])
def test_session_judged_families_get_the_session_reader(family: SchemaFamily) -> None:
    assert verdicts_for(family) is session_verdicts


def test_statistics_have_no_reader_and_the_error_names_the_family() -> None:
    with pytest.raises(ValueError, match=SchemaFamily.STATISTICS.value):
        verdicts_for(SchemaFamily.STATISTICS)
