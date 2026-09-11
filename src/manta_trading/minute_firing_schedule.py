"""When the minute acquisition pass fires — the one place that turns the
operator's ``MT_MINUTE_FIRING_DAYS`` setting into decisions.

The timer fires every day at ``MINUTE_PASS_FIRING_TIMES_UTC``; the setting
says which of those firings actually run. The pass asks "is today a firing
day?" (``is_firing_day``), the health check asks "when has the firing that
collects this session finished?" (``next_minute_firing_at``), and both read
the same parsed value, so changing cadence is one line in the environment
file and nothing else — no rebuild, no restart.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from manta_trading.constants import MINUTE_PASS_FIRING_TIMES_UTC

#: The spelling that means "every day". Any other value is a comma-separated
#: list of weekday names.
EVERY_DAY = "daily"

#: Weekday names accepted in the setting, indexed by ``date.weekday()``.
WEEKDAY_NAMES: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: A weekly cadence is the widest supported; searching this many days from
#: ``after`` therefore always finds a firing, and running off the end means
#: the weekday list is empty.
_SEARCH_DAYS = 8


def parse_firing_days(text: str) -> tuple[int, ...] | None:
    """``"daily"`` → None (every day); ``"Sat"`` / ``"Mon,Thu"`` → weekdays.

    Case-insensitive on the names; whitespace around commas is ignored. An
    empty string or an unknown name is an error, never a guess.
    """
    cleaned = text.strip()
    if cleaned.lower() == EVERY_DAY:
        return None
    if not cleaned:
        raise ValueError(
            f"MT_MINUTE_FIRING_DAYS is empty — use '{EVERY_DAY}' or weekday names "
            f"such as 'Sat' or 'Mon,Thu'"
        )
    by_name = {name.lower(): index for index, name in enumerate(WEEKDAY_NAMES)}
    days: list[int] = []
    for part in cleaned.split(","):
        name = part.strip().lower()
        if name not in by_name:
            raise ValueError(
                f"MT_MINUTE_FIRING_DAYS: unknown day {part.strip()!r}; use "
                f"'{EVERY_DAY}' or names from {', '.join(WEEKDAY_NAMES)}"
            )
        if by_name[name] not in days:
            days.append(by_name[name])
    return tuple(sorted(days))


def describe_firing_days(weekdays: tuple[int, ...] | None) -> str:
    """The setting's spelling for a parsed value, for log lines."""
    if weekdays is None:
        return EVERY_DAY
    return ",".join(WEEKDAY_NAMES[d] for d in weekdays)


def is_firing_day(day: date, weekdays: tuple[int, ...] | None) -> bool:
    """Whether a pass fired on ``day`` should run."""
    return weekdays is None or day.weekday() in weekdays


def next_minute_firing_at(
    after: datetime,
    *,
    weekdays: tuple[int, ...] | None,
    times: tuple[time, ...] = MINUTE_PASS_FIRING_TIMES_UTC,
) -> datetime:
    """The first firing that runs at or after ``after`` (an aware UTC instant)."""
    if after.tzinfo is None:
        raise ValueError("next_minute_firing_at needs an aware datetime")
    for offset in range(_SEARCH_DAYS):
        day = (after + timedelta(days=offset)).date()
        if not is_firing_day(day, weekdays):
            continue
        for firing in sorted(times):
            candidate = datetime.combine(day, firing, tzinfo=UTC)
            if candidate >= after:
                return candidate
    raise RuntimeError(
        f"no minute-pass firing within {_SEARCH_DAYS} days of {after:%Y-%m-%d} — "
        "the firing-day list or MINUTE_PASS_FIRING_TIMES_UTC is empty"
    )
