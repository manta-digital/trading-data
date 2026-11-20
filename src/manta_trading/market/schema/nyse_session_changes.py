"""The NYSE/NASDAQ session changes slice 174's calendar migration makes (#26).

Typed by hand from the same NYSE sources as ``nyse_calendar_history``, not
derived from its table, so the two transcriptions check each other. They are
the changes against production's rows before the migration: sessions populated
through 2028-12-29 from ``generate_holidays(cal, 2020, 2026)``, with no 2027-2028
holidays. The cutover refuses to apply when the live plan differs, and gap
reconciliation runs over the ``opened`` and ``closed`` dates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final


@dataclass(frozen=True)
class SessionChanges:
    """Session dates a calendar change touches, per kind.

    ``closed``: an existing session the new data removes. ``retimed``: an
    existing session whose open, close or extended close changes. ``opened``:
    a new session at or before the current horizon. ``appended``: a new
    session past it.
    """

    opened: frozenset[date] = field(default_factory=frozenset)
    closed: frozenset[date] = field(default_factory=frozenset)
    retimed: frozenset[date] = field(default_factory=frozenset)
    appended: frozenset[date] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        return not (self.opened or self.closed or self.retimed or self.appended)

    def reconciled_dates(self) -> frozenset[date]:
        """Dates whose gap rows change: a session appears or disappears."""
        return self.opened | self.closed


EXPECTED_SESSION_CHANGES: Final = SessionChanges(
    # NYSE observed no New Year's holiday for 2022.
    opened=frozenset({date(2021, 12, 31)}),
    closed=frozenset(
        {
            # National Day of Mourning (President Carter).
            date(2025, 1, 9),
            # 2027 full closures.
            date(2027, 1, 1),
            date(2027, 1, 18),
            date(2027, 2, 15),
            date(2027, 3, 26),
            date(2027, 5, 31),
            date(2027, 6, 18),
            date(2027, 7, 5),
            date(2027, 9, 6),
            date(2027, 11, 25),
            date(2027, 12, 24),
            # 2028 full closures (no New Year's observance).
            date(2028, 1, 17),
            date(2028, 2, 21),
            date(2028, 4, 14),
            date(2028, 5, 29),
            date(2028, 6, 19),
            date(2028, 7, 4),
            date(2028, 9, 4),
            date(2028, 11, 23),
            date(2028, 12, 25),
        }
    ),
    retimed=frozenset(
        {
            # Full days the rule generator shortened to 13:00.
            date(2020, 7, 2),
            date(2026, 7, 2),
            # 2027-2028 early closes.
            date(2027, 11, 26),
            date(2028, 7, 3),
            date(2028, 11, 24),
        }
    ),
)
