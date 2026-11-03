"""Unit tests for the CME hours eras (301 D1, D2, D5). No DB."""

from __future__ import annotations

from datetime import date

import pytest

from manta_trading.market.schema.seed_cme_calendar import CME_EQUITY_CALENDAR
from manta_trading.market.schema.seed_cme_hours import (
    CME_DATASET_START,
    CME_EQUITY_HOURS,
    CME_HOURS_ERAS,
    CME_METALS_HOURS,
    HoursEra,
)
from manta_trading.market.schema.seed_cme_metals_calendar import (
    CME_METALS_CALENDAR,
)

_CALENDARS = [
    pytest.param(CME_EQUITY_HOURS, CME_EQUITY_CALENDAR, id="equity"),
    pytest.param(CME_METALS_HOURS, CME_METALS_CALENDAR, id="metals"),
]
_UNTOUCHED_FROM = date(2020, 1, 1)


@pytest.mark.parametrize("era", CME_HOURS_ERAS, ids=lambda e: str(e.effective_from))
def test_every_era_cites_a_url(era: HoursEra) -> None:
    assert "https://web.archive.org/" in era.source


@pytest.mark.parametrize(("eras", "calendar"), _CALENDARS)
def test_eras_belong_to_their_calendar_in_date_order(
    eras: tuple[HoursEra, ...], calendar: dict[str, object]
) -> None:
    assert {era.calendar_id for era in eras} == {calendar["calendar_id"]}
    dates = [era.effective_from for era in eras]
    assert dates == sorted(set(dates))


@pytest.mark.parametrize(("eras", "calendar"), _CALENDARS)
def test_the_earliest_era_covers_the_dataset_start(
    eras: tuple[HoursEra, ...], calendar: dict[str, object]
) -> None:
    assert eras[0].effective_from <= CME_DATASET_START


@pytest.mark.parametrize(("eras", "calendar"), _CALENDARS)
def test_the_latest_era_is_the_calendar_row(
    eras: tuple[HoursEra, ...], calendar: dict[str, object]
) -> None:
    latest = eras[-1]
    assert latest.market_open == calendar["market_open"]
    assert latest.market_close == calendar["market_close"]
    assert latest.sunday_open == calendar["market_open"]


@pytest.mark.parametrize(("eras", "calendar"), _CALENDARS)
def test_no_era_starts_in_the_untouched_range(
    eras: tuple[HoursEra, ...], calendar: dict[str, object]
) -> None:
    assert all(era.effective_from < _UNTOUCHED_FROM for era in eras)


def test_the_dataset_start_is_the_first_databento_cme_date() -> None:
    assert CME_DATASET_START == date(2010, 6, 6)
