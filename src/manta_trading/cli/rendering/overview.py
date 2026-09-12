"""Rendering for ``mt data overview``.

Plain text in fixed columns rather than a Rich table: this is the screen an
operator reads first, often over ssh in a narrow terminal, and alignment that
survives copy-paste into an issue is worth more here than borders.

Every function is pure — it takes the built :class:`Overview` and returns
strings — so what the operator sees is testable without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from manta_trading.cli.commands.overview import (
    NEVER_RUN,
    NO_ACCOUNTING,
    LastRun,
    Overview,
    PassLine,
    RunningRow,
    SourceFreshness,
)
from manta_trading.data.acquisition.pass_runs import PassRunOutcome

_OUTCOME_TEXT: dict[PassRunOutcome, str] = {
    PassRunOutcome.COMPLETE: "complete",
    PassRunOutcome.COMPLETE_QUOTA: "complete (quota)",
    PassRunOutcome.INCOMPLETE: "incomplete",
    PassRunOutcome.PROVIDER_UNAVAILABLE: "provider unavailable",
    PassRunOutcome.FAILED: "failed",
}

# Every outcome must render — a new member cannot print as its raw enum value.
assert set(_OUTCOME_TEXT) == set(PassRunOutcome), (
    "the overview outcome text is not exhaustive — update it after adding a "
    "PassRunOutcome member"
)

_KIND_WIDTH = 10
_CADENCE_WIDTH = 14
_NOW_WIDTH = 30
_SOURCE_WIDTH = 16
_SCREEN_WIDTH = 100
"""Target width. Wide enough for the last-run column, narrow enough to paste
into an issue without wrapping."""


def outcome_text(outcome: PassRunOutcome) -> str:
    """The operator-facing spelling of an outcome.

    ``COMPLETE_QUOTA`` reads "complete (quota)" because that is what it means:
    the pass did its job and the allowance ran out, which is the designed
    steady state rather than a fault.
    """
    return _OUTCOME_TEXT[outcome]


def _stamp(at: datetime) -> str:
    """A weekday-and-time stamp: enough to place a run without a full date."""
    return f"{at:%a %m-%d %H:%M}"


def _age(now: datetime, then: datetime) -> str:
    """How long ago, in the largest unit that stays readable."""
    seconds = int((now - then).total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def render_running(row: RunningRow, now: datetime) -> str:
    """One still-open run: what it is doing, since when, and whether it lives."""
    if row.abandoned:
        return f"ABANDONED (pid {row.pid} gone, since {_stamp(row.since)})"
    parts = ["RUNNING"]
    if row.phase:
        parts.append(row.phase)
    if row.done is not None and row.total:
        parts.append(f"{row.done:,}/{row.total:,}")
    detail = f"since {_stamp(row.since)}"
    if row.progress_at is not None:
        detail += f", progress {_age(now, row.progress_at)}"
    parts.append(f"({detail})")
    return " ".join(parts)


def render_last(last: LastRun | None, *, now: datetime | None = None) -> str:
    """The last ended run, as one line.

    A non-zero exit code is named; a zero one is not. The outcome already
    says the run was fine, and a column of "exit 0" trains the eye to skip
    the place where the interesting number appears.
    """
    if last is None:
        return NEVER_RUN
    started = (
        f"{last.started_at:%H:%M}"
        if now is not None and last.started_at.date() == now.date()
        else _stamp(last.started_at)
    )
    text = f"{started}–{last.ended_at:%H:%M}  {outcome_text(last.outcome)}"
    if last.exit_code:
        text += f"  exit {last.exit_code}"
    return text


def render_pass_line(line: PassLine, now: datetime) -> list[str]:
    """The PASSES rows for one kind: a header plus any continuation lines.

    Columns are for scanning, so a value too wide for its column moves to its
    own line rather than shoving the last-run column out of alignment — the
    one thing that makes a fixed-width screen unreadable.
    """
    running = list(line.running)
    prefix = f"{line.kind.value:<{_KIND_WIDTH}} {line.cadence:<{_CADENCE_WIDTH}}"
    indent = " " * (_KIND_WIDTH + _CADENCE_WIDTH + 2)
    last_text = render_last(line.last, now=now)

    first_now = render_running(running[0], now) if running else "idle"
    if len(first_now) <= _NOW_WIDTH:
        lines = [f"{prefix} {first_now:<{_NOW_WIDTH}} {last_text}".rstrip()]
    else:
        lines = [f"{prefix} {first_now}".rstrip()]
        lines.append(f"{indent}{'':<{_NOW_WIDTH}} {last_text}".rstrip())

    # Every other open run of this kind gets its own line: two live passes of
    # one kind is exactly the state an operator needs shown, not summarised.
    for row in running[1:]:
        lines.append(f"{indent}{render_running(row, now)}")
    if line.last is not None and line.last.failed and line.last.detail:
        lines.append(f"{indent}{line.last.detail}")
    return lines


def _next_stamp(at: datetime, now: datetime) -> str:
    """A firing later today needs only its time; a later one needs its day."""
    return f"{at:%H:%M}" if at.date() == now.date() else _stamp(at)


def render_next_firings(overview: Overview, *, width: int = _SCREEN_WIDTH) -> list[str]:
    """When each pass fires next, wrapped to stay on the screen."""
    parts = [
        f"{line.kind.value} {_next_stamp(line.next_firing, overview.now)}"
        for line in overview.passes
        if line.next_firing is not None
    ]
    if not parts:
        return []
    label = f"{'next':<{_KIND_WIDTH}} "
    indent = " " * len(label)
    lines: list[str] = []
    current = label + parts[0]
    for part in parts[1:]:
        candidate = f"{current} · {part}"
        if len(candidate) > width:
            lines.append(current)
            current = indent + part
        else:
            current = candidate
    lines.append(current)
    return lines


def render_source_lines(
    sources: Sequence[SourceFreshness], now: datetime
) -> list[str]:
    """The per-source freshness rows, without a header.

    Shared by the overview's SOURCES block and ``mt data status``'s default
    summary, so one change to how freshness reads reaches both.
    """
    lines = []
    for source in sources:
        if source.newest is None:
            newest = "none"
        else:
            age = _age(now, source.newest)
            newest = f"{source.newest:%Y-%m-%d %H:%M} UTC ({age})"
        lines.append(f"{source.name:<{_SOURCE_WIDTH}} {newest}")
    return lines


def render_status_sources(
    sources: Sequence[SourceFreshness], *, now: datetime
) -> str:
    """The SOURCES block for ``mt data status``'s default summary."""
    header = f"{'SOURCES':<{_SOURCE_WIDTH}} newest"
    return "\n".join([header, *render_source_lines(sources, now)])


def render_sources(overview: Overview) -> list[str]:
    """The SOURCES block: the newest row in each source, and its age."""
    header = f"{'SOURCES':<{_SOURCE_WIDTH}} newest"
    if overview.health_verdict is not None and overview.health_at is not None:
        header += (
            f"{'':>22}health ({overview.health_at:%H:%M} UTC): "
            f"{overview.health_verdict}"
        )
    return [header, *render_source_lines(overview.sources, overview.now)]


def render_universe(overview: Overview) -> list[str]:
    """The universe line, with when it was computed."""
    if overview.universe is None:
        return [f"{'minute universe':<{_SOURCE_WIDTH}} {NO_ACCOUNTING}"]
    stamp = (
        f"(accounting {overview.universe_at:%m-%d %H:%M} UTC) "
        if overview.universe_at is not None
        else ""
    )
    summary = overview.universe
    prefix = "minute universe: "
    if summary.startswith(prefix):
        # The accounting summary names itself; the label already did.
        summary = summary[len(prefix) :]
    return _wrapped(f"{'minute universe':<{_SOURCE_WIDTH}}", f"{stamp}{summary}")


def _wrapped(label: str, body: str, *, width: int = _SCREEN_WIDTH) -> list[str]:
    """``label body``, wrapped on spaces with the continuation lines indented."""
    indent = " " * (len(label) + 1)
    lines: list[str] = []
    current = f"{label} "
    for word in body.split(" "):
        if len(current) + len(word) > width and current.strip():
            lines.append(current.rstrip())
            current = indent
        current += f"{word} "
    lines.append(current.rstrip())
    return lines


def render_overview(overview: Overview) -> str:
    """The whole screen."""
    title = "manta-trading overview"
    stamp = f"{overview.now:%Y-%m-%d %H:%M} UTC"
    lines = [
        f"{title}{stamp:>{_SCREEN_WIDTH - len(title)}}",
        "",
        f"{'PASSES':<{_KIND_WIDTH}} {'cadence':<{_CADENCE_WIDTH}} "
        f"{'now':<{_NOW_WIDTH}} last run",
    ]
    for line in overview.passes:
        lines.extend(render_pass_line(line, overview.now))
    lines.extend(render_next_firings(overview))
    lines.append("")
    lines.extend(render_sources(overview))
    lines.append("")
    lines.append(f"{'EODHD credits':<{_SOURCE_WIDTH}} {overview.credits_text}")
    lines.extend(render_universe(overview))
    return "\n".join(lines)


def overview_payload(overview: Overview) -> dict:
    """The ``--json`` shape: the gathered facts as one object."""
    return {
        "now": overview.now.isoformat(),
        "passes": [
            {
                "pass": line.kind.value,
                "cadence": line.cadence,
                "running": [
                    {
                        "phase": row.phase,
                        "done": row.done,
                        "total": row.total,
                        "since": row.since.isoformat(),
                        "progress_at": (
                            row.progress_at.isoformat()
                            if row.progress_at is not None
                            else None
                        ),
                        "hostname": row.hostname,
                        "pid": row.pid,
                        "abandoned": row.abandoned,
                    }
                    for row in line.running
                ],
                "last_run": (
                    {
                        "started_at": line.last.started_at.isoformat(),
                        "ended_at": line.last.ended_at.isoformat(),
                        "outcome": line.last.outcome.value,
                        "exit_code": line.last.exit_code,
                        "detail": line.last.detail,
                    }
                    if line.last is not None
                    else None
                ),
                "next_firing": (
                    line.next_firing.isoformat()
                    if line.next_firing is not None
                    else None
                ),
            }
            for line in overview.passes
        ],
        "sources": [
            {
                "name": source.name,
                "newest": (
                    source.newest.isoformat() if source.newest is not None else None
                ),
            }
            for source in overview.sources
        ],
        "health": {
            "verdict": overview.health_verdict,
            "at": (
                overview.health_at.isoformat()
                if overview.health_at is not None
                else None
            ),
        },
        "credits": (
            {
                "used": overview.credits.used,
                "daily_limit": overview.credits.daily_limit,
                "extra": overview.credits.extra,
                "remaining": overview.credits.remaining,
            }
            if overview.credits is not None
            else None
        ),
        "credits_text": overview.credits_text,
        "universe": {
            "summary": overview.universe,
            "at": (
                overview.universe_at.isoformat()
                if overview.universe_at is not None
                else None
            ),
        },
    }
