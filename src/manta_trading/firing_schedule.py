"""When each recurring pass fires — the one place that turns cadence into
instants.

For the minute pass this is also where the operator's
``MT_MINUTE_FIRING_DAYS`` setting becomes decisions: the timer fires every
day at ``MINUTE_PASS_FIRING_TIMES_UTC`` and the setting says which of those
firings actually run. The pass asks "is today a firing day?"
(:func:`is_firing_day`), the health check asks "when has the firing that
collects this session finished?" (:func:`next_minute_firing_at`), and both
read the same parsed value, so changing cadence is one line in the
environment file and nothing else — no rebuild, no restart.

Slice 922 generalised the "when next" question to every pass kind, because
the overview answers it for all five: :class:`FiringSchedule` pairs times of
day with the weekdays they run on, :func:`next_firing_at` searches it, and
:func:`schedule_for` is the single place each kind's cadence is assembled.
The times themselves live in ``constants.py`` beside the assertion that they
match the shipped timer units.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from manta_trading.constants import (
    ACCOUNTING_PASS_FIRING_TIMES_UTC,
    DAILY_PASS_FIRING_TIMES_UTC,
    HEALTH_FIRING_MINUTE,
    KALSHI_PASS_FIRING_MINUTE,
    MINUTE_PASS_FIRING_TIMES_UTC,
)
from manta_trading.data.acquisition.pass_runs import PassKind

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
    """The first firing that runs at or after ``after`` (an aware UTC instant).

    The minute pass's own spelling of :func:`next_firing_at`, kept because
    its callers pass weekdays and times rather than a schedule. The search
    itself is not repeated here — slice 922 generalised it, and holding two
    copies of one algorithm is how they drift (922 re-review F005).
    """
    if after.tzinfo is None:
        # Checked here as well so the message names the function the caller
        # actually called; the delegate would say "next_firing_at".
        raise ValueError("next_minute_firing_at needs an aware datetime")
    return next_firing_at(after, FiringSchedule(times_utc=times, weekdays=weekdays))


# ---------------------------------------------------------------------------
# Generalised schedules (slice 922)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FiringSchedule:
    """When one pass fires: times of day, and which weekdays they run on.

    ``weekdays`` None means every day, matching the ``MT_MINUTE_FIRING_DAYS``
    spelling so the minute pass needs no special case here.
    """

    times_utc: tuple[time, ...]
    weekdays: tuple[int, ...] | None = None

    @classmethod
    def hourly(cls, minute: int) -> FiringSchedule:
        """Every hour at ``minute`` past — the Kalshi and health cadence."""
        return cls(tuple(time(hour, minute) for hour in range(24)))

    def describe(self) -> str:
        """One line for the overview's cadence column."""
        if self._is_hourly():
            return f"hourly :{self.times_utc[0].minute:02d}"
        times = ", ".join(f"{t:%H:%M}" for t in sorted(self.times_utc))
        if self.weekdays is None:
            return times
        return f"{times} on {describe_firing_days(self.weekdays)}"

    def _is_hourly(self) -> bool:
        """True when this fires every hour at one fixed minute past."""
        if self.weekdays is not None or len(self.times_utc) != 24:
            return False
        minutes = {t.minute for t in self.times_utc}
        hours = {t.hour for t in self.times_utc}
        return len(minutes) == 1 and hours == set(range(24))


def next_firing_at(after: datetime, schedule: FiringSchedule) -> datetime:
    """The first firing at or after ``after`` (an aware UTC instant).

    Raises when the schedule can never fire — an empty time list or an empty
    weekday list — rather than returning a plausible-looking instant.
    """
    if after.tzinfo is None:
        raise ValueError("next_firing_at needs an aware datetime")
    for offset in range(_SEARCH_DAYS):
        day = (after + timedelta(days=offset)).date()
        if not is_firing_day(day, schedule.weekdays):
            continue
        for firing in sorted(schedule.times_utc):
            candidate = datetime.combine(day, firing, tzinfo=UTC)
            if candidate >= after:
                return candidate
    raise RuntimeError(
        f"no firing within {_SEARCH_DAYS} days of {after:%Y-%m-%d} — the "
        "schedule's weekday or time list is empty"
    )


def schedule_for(
    kind: PassKind, minute_firing_days: tuple[int, ...] | None = None
) -> FiringSchedule:
    """The cadence of one pass kind, assembled in exactly one place.

    Args:
        kind: Which pass.
        minute_firing_days: The operator's ``MT_MINUTE_FIRING_DAYS``, which
            only the minute pass observes. Ignored for every other kind.
    """
    if kind is PassKind.MINUTE:
        return FiringSchedule(MINUTE_PASS_FIRING_TIMES_UTC, minute_firing_days)
    if kind is PassKind.DAILY:
        return FiringSchedule(DAILY_PASS_FIRING_TIMES_UTC)
    if kind is PassKind.KALSHI:
        return FiringSchedule.hourly(KALSHI_PASS_FIRING_MINUTE)
    if kind is PassKind.HEALTH:
        return FiringSchedule.hourly(HEALTH_FIRING_MINUTE)
    if kind is PassKind.ACCOUNTING:
        return FiringSchedule(ACCOUNTING_PASS_FIRING_TIMES_UTC)
    # Not reachable while the branches above are exhaustive; a new PassKind
    # member lands here loudly rather than acquiring a silent default.
    raise AssertionError(f"no firing schedule defined for pass kind {kind!r}")
