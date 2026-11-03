"""Tests for the window and finding logic of ``scripts/check_cme_history_counts.py``."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def check() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("check_cme_history_counts")


class _Session:
    def __init__(self, day: date, open_utc: datetime, close_utc: datetime) -> None:
        self.session_date = day
        self.open_utc = open_utc
        self.close_utc = close_utc


def _session(day: date, hours_open: int = 23) -> _Session:
    open_utc = datetime(day.year, day.month, day.day, 22, tzinfo=UTC) - timedelta(
        days=1
    )
    return _Session(day, open_utc, open_utc + timedelta(hours=hours_open))


def _run(
    check: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    counts: dict[str, int | Exception],
    days: list[date],
) -> int:
    """Run over ``days``; ``counts`` maps 'session'/'gap' to the answer."""
    sessions = [_session(d) for d in days]

    class _Settings:
        databento_api_key = "k"

    class _Client:
        class metadata:  # noqa: N801
            TIMEOUT = 0

    def fake_count(_c: Any, _p: str, start: datetime, end: datetime) -> int:
        kind = "session" if (end - start) >= timedelta(hours=20) else "gap"
        answer = counts[kind]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(check, "Settings", _Settings)
    monkeypatch.setattr(check.databento, "Historical", lambda key: _Client())
    monkeypatch.setattr(check, "_sessions", lambda *_a: sessions)
    monkeypatch.setattr(check, "_count", fake_count)
    return check.run("CME_EQUITY", "ES.FUT", days[0], days[-2])


DAYS = [date(2012, 3, 5), date(2012, 3, 6), date(2012, 3, 7)]


def test_bars_in_sessions_and_none_in_gaps_is_clean(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gap is 1h here, the session 23h; the fake keys on window length.
    assert _run(check, monkeypatch, {"session": 5, "gap": 0}, DAYS) == check.EXIT_CLEAN


def test_a_session_with_no_bars_is_a_finding(
    check: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(check, monkeypatch, {"session": 0, "gap": 0}, DAYS)
    assert code == check.EXIT_FINDINGS
    assert "NO-BARS" in capsys.readouterr().out


def test_bars_in_a_gap_are_a_finding(
    check: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run(check, monkeypatch, {"session": 5, "gap": 2}, DAYS)
    assert code == check.EXIT_FINDINGS
    assert "GAP-BARS" in capsys.readouterr().out


def test_a_failed_call_is_a_finding_never_a_zero(
    check: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    boom = check.ProviderTransientError("HTTP 504")
    code = _run(check, monkeypatch, {"session": boom, "gap": 0}, DAYS)
    assert code == check.EXIT_FINDINGS
    assert "FAILED" in capsys.readouterr().out


def test_a_refusal_stops_the_run(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusal = check.ProviderAuthError("401")
    assert (
        _run(check, monkeypatch, {"session": refusal, "gap": 0}, DAYS)
        == check.EXIT_STOPPED
    )
