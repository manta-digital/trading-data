"""``mt data calendars sessions`` — list a calendar's stored sessions (221 D9).

A debug view of ``trading_sessions`` through ``TradingCalendar.sessions_between``:
one row per session with its open and close in the calendar's time zone and in
UTC, its length, and the exception (holiday) that shaped it, if any.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer

from manta_trading.cli.output import make_table, print_error, print_result
from manta_trading.data.base.session_index import Session
from manta_trading.data.base.trading_calendar import (
    CalendarNotFoundError,
    OutOfPopulatedRangeError,
    TradingCalendar,
)

EXIT_UNCONFIGURED = 1
"""Exit code when no database URL is configured."""

EXIT_UNKNOWN_CALENDAR = 1
"""Exit code when --calendar names no row in ``trading_calendars``."""

EXIT_OUT_OF_RANGE = 1
"""Exit code when the requested dates fall outside the populated sessions."""


def data_calendar_sessions(
    ctx: typer.Context,
    calendar: Annotated[str, typer.Option("--calendar", help="Calendar ID.")],
    from_date: Annotated[
        datetime,
        typer.Option(
            "--from", formats=["%Y-%m-%d"], help="First session date (YYYY-MM-DD)."
        ),
    ],
    to_date: Annotated[
        datetime,
        typer.Option(
            "--to", formats=["%Y-%m-%d"], help="Last session date (YYYY-MM-DD)."
        ),
    ],
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show the sessions dated --from through --to (sessions are dated by close)."""
    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(EXIT_UNCONFIGURED)

    cal = TradingCalendar(calendar, str(settings.timescale_db_url))
    try:
        rows = _session_rows(cal, from_date.date(), to_date.date())
    except CalendarNotFoundError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(EXIT_UNKNOWN_CALENDAR) from exc
    except OutOfPopulatedRangeError as exc:
        print_error(str(exc), json_mode=json_output)
        raise typer.Exit(EXIT_OUT_OF_RANGE) from exc
    finally:
        cal.close()

    if json_output:
        print_result(rows, json_mode=True)
        return
    print_result(_render_table(calendar, rows), json_mode=False)
    print_result(f"\n{len(rows)} session(s)", json_mode=False)


def _session_rows(
    cal: TradingCalendar, first: date, last: date
) -> list[dict[str, Any]]:
    """One output row per session dated ``first`` through ``last``."""
    names = {
        h.holiday_date: h.holiday_name
        for year in range(first.year, last.year + 1)
        for h in cal.get_holidays(year)
    }
    if cal.timezone is None:
        raise ValueError(f"calendar '{cal.calendar_id}' has no time zone loaded")
    tz = cal.timezone

    # Sessions are dated by close, so the one dated `first` opened before
    # midnight; bound the window by midnights and filter by date.
    start = datetime.combine(first, time(0), tzinfo=tz)
    end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz)
    _, last_close = cal.populated_span()
    if last_close is None or last > last_close.astimezone(tz).date():
        raise OutOfPopulatedRangeError(cal.calendar_id, end, *cal.populated_span())
    sessions = cal.sessions_between(start, min(end, last_close))
    return [
        _row(s, tz, names.get(s.session_date))
        for s in sessions
        if first <= s.session_date <= last
    ]


def _row(session: Session, tz: ZoneInfo, exception: str | None) -> dict[str, Any]:
    minutes = int((session.close_utc - session.open_utc).total_seconds() // 60)
    return {
        "session_date": session.session_date.isoformat(),
        "open_local": session.open_utc.astimezone(tz).isoformat(),
        "close_local": session.close_utc.astimezone(tz).isoformat(),
        "open_utc": session.open_utc.isoformat(),
        "close_utc": session.close_utc.isoformat(),
        "duration": f"{minutes // 60}h{minutes % 60:02d}m",
        "exception": exception,
    }


def _render_table(calendar: str, rows: list[dict[str, Any]]) -> Any:
    table = make_table(
        f"{calendar} sessions",
        [
            ("Date", "bold"),
            ("Open (local)", ""),
            ("Close (local)", ""),
            ("Open (UTC)", ""),
            ("Close (UTC)", ""),
            ("Length", ""),
            ("Exception", ""),
        ],
    )
    for r in rows:
        table.add_row(
            r["session_date"],
            *(
                _short(r[key])
                for key in ("open_local", "close_local", "open_utc", "close_utc")
            ),
            r["duration"],
            r["exception"] or "",
        )
    return table


def _short(iso: str) -> str:
    """``MM-DD HH:MM`` for a table cell; the JSON output keeps full ISO."""
    return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
