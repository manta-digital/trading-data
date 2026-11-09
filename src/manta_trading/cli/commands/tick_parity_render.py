"""Output of ``mt data tick parity``: text and JSON from a finished report (306 D7).

The report is complete before anything is written, so neither form can be cut
short. The text form lists no minutes; ``--json`` adds each session's
``minutes_listed``.
"""

from __future__ import annotations

import json
from typing import Any

from manta_trading.cli.commands.tick_get_writers import (
    head_lines,
    iso_day,
    target_dict,
)
from manta_trading.data.tick.bar_parity import (
    COMPARED_FIELDS,
    MinuteClass,
    MinuteRecord,
    Ohlcv,
)
from manta_trading.data.tick.bar_parity_report import (
    ParityReport,
    SessionReport,
    SkippedSession,
)
from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.tick_format import cell_value, ts_text

#: The ``what`` word in the header line, where ``get`` names ticks or a bar size.
PARITY_WHAT = "parity"


def _side(values: Ohlcv | None) -> dict[str, Any] | None:
    if values is None:
        return None
    return {
        name: cell_value(name, v)
        for name, v in zip(COMPARED_FIELDS, values, strict=True)
    }


def _minute(minute: MinuteRecord) -> dict[str, Any]:
    return {
        "time": ts_text(minute.bucket),
        "class": minute.cls.value,
        "fields": list(minute.fields),
        "at_close": minute.at_close,
        "tick": _side(minute.tick),
        "provider": _side(minute.provider),
    }


def _counts(session: SessionReport) -> dict[str, int]:
    return {cls.value: session.counts[cls] for cls in MinuteClass}


def _session_dict(session: SessionReport) -> dict[str, Any]:
    return {
        "session_date": session.session_date.isoformat(),
        "counts": _counts(session),
        "close_stamped": session.close_stamped,
        "close_stamped_agree": session.close_stamped_agree,
        "tick_volume": session.tick_volume,
        "provider_volume": session.provider_volume,
        "minutes_listed": [_minute(m) for m in session.minutes_listed],
    }


def _skipped_dict(skipped: SkippedSession) -> dict[str, str]:
    return {
        "session_date": skipped.session_date.isoformat(),
        "tick_status": skipped.tick_status.value,
        "bar_status": skipped.bar_status.value,
    }


def json_text(plan: GetPlan, report: ParityReport, exit_code: int) -> str:
    body = {
        "target": target_dict(plan),
        "start": iso_day(plan.start),
        "end": iso_day(plan.end),
        "sessions": [_session_dict(s) for s in report.sessions],
        "skipped": [_skipped_dict(s) for s in report.skipped],
        "exit_code": exit_code,
    }
    return json.dumps(body) + "\n"


def _session_line(session: SessionReport) -> str:
    counts = "  ".join(f"{cls.value} {session.counts[cls]}" for cls in MinuteClass)
    return (
        f"{session.session_date}  {counts}  "
        f"close-stamped {session.close_stamped} (agree {session.close_stamped_agree})  "
        f"volume tick {session.tick_volume} provider {session.provider_volume}"
    )


def _skipped_line(skipped: SkippedSession) -> str:
    return (
        f"skipped {skipped.session_date}  tick {skipped.tick_status.value}  "
        f"bar {skipped.bar_status.value}"
    )


def text(plan: GetPlan, report: ParityReport) -> str:
    lines = [
        *head_lines(plan, PARITY_WHAT, None),
        *(_session_line(s) for s in report.sessions),
        *(_skipped_line(s) for s in report.skipped),
        f"{len(report.sessions)} session(s) compared, {len(report.skipped)} skipped",
    ]
    return "\n".join(lines) + "\n"
