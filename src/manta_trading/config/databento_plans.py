"""What each Databento subscription includes (slice 300, D5, D6).

A request the plan includes costs nothing at the margin, so the spend guard does
not count it. The table is a typed module, not an environment value: its
contents change only when Databento's pricing changes, and that needs a reviewed
commit with a test. Which plan the account holds is ``MT_TICK_DATABENTO_PLAN``
(``Settings.tick_databento_plan``); absent means no subscription.

Source: the Databento CME pricing page, read 2026-10-28. The window is a count
of days back from the day of the request; ``None`` means all history.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Final

from manta_trading.data.tick.constants import CME_DATASET, TickSchema


class DatabentoPlan(StrEnum):
    STANDARD = "standard"


#: The one dataset every plan below covers.
PLAN_DATASET: Final[str] = CME_DATASET

_STANDARD_TICK_WINDOW = timedelta(days=365)

#: Plan -> schema -> included history window; total over ``TickSchema``.
PLAN_INCLUDED_HISTORY: Final[
    Mapping[DatabentoPlan, Mapping[TickSchema, timedelta | None]]
] = {
    DatabentoPlan.STANDARD: {
        TickSchema.TRADES: _STANDARD_TICK_WINDOW,
        TickSchema.TBBO: _STANDARD_TICK_WINDOW,
        TickSchema.MBP_1: _STANDARD_TICK_WINDOW,
        TickSchema.DEFINITION: None,
    },
}
