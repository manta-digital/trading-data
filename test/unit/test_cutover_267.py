"""The 267 cutover script's report parsing, against a journal excerpt (slice
267, Task 11.1 — the 265 script shipped without one).

``scripts/`` is not a package: the modules are loaded by path with the
directory on ``sys.path`` so ``cutover_267_historical`` finds
``cutover_common``. Nothing here touches a host, a journal, or ``sudo``.
"""

# ruff: noqa: E501 — journal lines are quoted at their real length
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
T0 = datetime(2026, 9, 2, 3, 20, tzinfo=UTC)


@pytest.fixture(scope="module")
def cutover() -> Any:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "cutover_267_historical", SCRIPTS / "cutover_267_historical.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve ``sys.modules[cls.__module__]`` under
    # ``from __future__ import annotations``; register before executing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _firing(cutover: Any, *messages: tuple[int, str]) -> Any:
    """Entries at ``T0 + seconds`` — the shapes the code under test emits."""
    return cutover.Firing([(T0 + timedelta(seconds=s), m) for s, m in messages])


JOURNAL_AFTER_WALK: list[tuple[int, str]] = [
    (0, "kalshi client mode=authenticated budget=1000/min base_url=https://x"),
    (1, "kalshi pass started run_id=r1 mode=authenticated budget=1000/min phases=..."),
    (120, "kalshi HTTP 429 (transient, attempt 1/4) on /markets; backing off 1.0s"),
    (900, "kalshi historical cap=30000 (1000/min × 30 min, mode=authenticated)"),
    (
        901,
        "kalshi historical phase started run_id=r1 cap=30000 floor=2026-01-01T00:00:00+00:00 rule: r",
    ),
    (
        902,
        "kalshi historical candles: cutoff=2026-07-03T00:00:00+00:00 pending=1000 (limit 1000)",
    ),
    (950, "historical: KXGONE-1 skipped — 404 market not found"),
    (
        990,
        "kalshi historical slow market KXSLOW-9: 41.2s for 12000 candles (threshold 30s; ...)",
    ),
    (
        1000,
        "kalshi historical first run: tape from live floor 2026-07-01T00:00:00+00:00 down to 2026-01-01T00:00:00+00:00",
    ),
    (
        1060,
        "historical window 2026-06-30T23:00:00+00:00→2026-07-01T00:00:00+00:00 pages 210 fetched 209000 written 120000 unknown 20000 excluded 69000",
    ),
    (
        1100,
        "historical window 2026-06-30T22:00:00+00:00→2026-06-30T23:00:00+00:00 pages 100 fetched 99000 written 60000 unknown 9000 excluded 30000",
    ),
    (1101, "historical unknown markets: KXMVECROSSCATEGORY 29,000"),
    (
        1102,
        "kalshi pass finished outcome=partial duration=1500000 ms phases: catalog=ok candles=ok trades=ok historical=partial",
    ),
]


def _status(
    *, remaining: int, tape_from: str | None, walked: bool = True
) -> dict[str, Any]:
    return {
        "markets": 4_000_000,
        "candles": {"behind_cutoff_uncollected": remaining},
        "historical": {
            "archive_walked": walked,
            "tape_from": tape_from,
            "tape_to": "2026-07-01T00:00:00+00:00",
            "floor": "2026-01-01T00:00:00+00:00",
        },
    }


@pytest.fixture(scope="module")
def common(cutover: Any) -> Any:
    """``cutover_common``, importable once the ``cutover`` fixture has put
    ``scripts/`` on ``sys.path``."""
    import cutover_common

    return cutover_common


class TestVerifyMigrateTarget:
    """``migrate()``'s target check (267 code review, production-db-safety):
    the checkout's maintenance URL must name the database the production
    units use, or the script refuses before any DDL."""

    # The real file shapes: comments, an ``export``, quotes, a default port.
    CHECKOUT = (
        "# dev checkout .env\n"
        "export MT_TIMESCALE_MAINTENANCE_URL='postgresql://mig:pw@127.0.0.1:5432/market'\n"
    )
    PRODUCTION = "MT_TIMESCALE_DB_URL=postgresql://app:pw@localhost/market\n"

    def test_same_database_passes_and_names_it(self, common: Any):
        # 127.0.0.1:5432 vs localhost with the default port: equal.
        assert common.verify_migrate_target(self.CHECKOUT, self.PRODUCTION) == "market"

    def test_wrong_host_refuses(self, common: Any):
        other = "MT_TIMESCALE_DB_URL=postgresql://app:pw@192.168.1.143:5432/market\n"
        with pytest.raises(common.CutoverError, match="refusing to migrate"):
            common.verify_migrate_target(self.CHECKOUT, other)

    def test_wrong_database_name_refuses(self, common: Any):
        renamed = self.PRODUCTION.replace("/market", "/mt_rehearsal_267")
        with pytest.raises(common.CutoverError, match="refusing to migrate"):
            common.verify_migrate_target(self.CHECKOUT, renamed)

    def test_missing_key_refuses(self, common: Any):
        with pytest.raises(common.CutoverError, match="MT_TIMESCALE_MAINTENANCE_URL"):
            common.verify_migrate_target("# empty\n", self.PRODUCTION)
        with pytest.raises(common.CutoverError, match="MT_TIMESCALE_DB_URL"):
            common.verify_migrate_target(self.CHECKOUT, "")


class TestParseFiring:
    def test_every_line_kind_is_read(self, cutover: Any):
        facts = cutover.parse_firing(_firing(cutover, *JOURNAL_AFTER_WALK))
        assert (facts.mode, facts.budget, facts.cap) == ("authenticated", 1000, 30000)
        assert facts.first_run == (
            "2026-07-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        )
        assert [w["pages"] for w in facts.windows] == ["210", "100"]
        # Window timing: from the phase start line to the first window line,
        # then window to window.
        assert [round(w["seconds"]) for w in facts.windows] == [159, 40]
        assert facts.windows[1]["per_page"] == pytest.approx(0.4)
        assert facts.slow_markets == [("KXSLOW-9", 41.2)]
        assert facts.skipped == ["KXGONE-1"]
        assert facts.outcome == "partial" and facts.duration_ms == 1_500_000
        assert "historical=partial" in facts.phases
        assert (facts.http_429, facts.http_429_escalated) == (1, 0)
        assert facts.archive_done is None and facts.archive_capped_pages is None

    def test_archive_lines(self, cutover: Any):
        done = cutover.parse_firing(
            _firing(
                cutover,
                (
                    5,
                    "kalshi historical archive walk done pages=812 markets=811900 written=700000 parent item errors=3",
                ),
            )
        )
        assert done.archive_done == {"pages": 812, "markets": 811900, "written": 700000}
        capped = cutover.parse_firing(
            _firing(
                cutover,
                (
                    5,
                    "kalshi historical cap reached during the archive walk pages=1500; cursor saved, the next run resumes it",
                ),
            )
        )
        assert capped.archive_capped_pages == 1500

    def test_escalated_429_is_counted(self, cutover: Any):
        facts = cutover.parse_firing(
            _firing(
                cutover,
                (1, "kalshi HTTP 429 (transient, attempt 1/4) on /x"),
                (2, "kalshi HTTP 429 (transient, attempt 2/4) on /x"),
            )
        )
        assert (facts.http_429, facts.http_429_escalated) == (2, 1)


class TestEvaluate:
    def test_first_firing_after_the_walk(self, cutover: Any):
        facts = cutover.parse_firing(_firing(cutover, *JOURNAL_AFTER_WALK))
        checks = cutover.evaluate(
            facts,
            before=_status(remaining=8394, tape_from=None),
            after=_status(remaining=7395, tape_from="2026-06-30T22:00:00+00:00"),
            walk_firing=False,
        )
        by_text = {text: ok for ok, text in checks}
        assert by_text[next(t for t in by_text if t.startswith("client mode="))] is True
        assert by_text["cap=30000 (expected 30000)"] is True
        assert any(
            ok and "phases:" in t and "KXGONE-1" in t for t, ok in by_text.items()
        )
        assert any(ok and "seeded at the live floor" in t for t, ok in by_text.items())
        assert any(
            ok and "descended 2.00 h over 2 windows" in t for t, ok in by_text.items()
        )
        assert any(ok and "completed 999" in t for t, ok in by_text.items())
        assert any(
            ok and "pass duration 25.0 min < 45 min" in t for t, ok in by_text.items()
        )
        assert any(
            ok and "slowest window 159 s for 210 pages" in t
            for t, ok in by_text.items()
        )
        # The one ❌ this excerpt carries: a market over the slow threshold.
        assert by_text["slowest market KXSLOW-9 41.2 s (over 30 s)"] is False

    def test_public_mode_and_capped_walk_are_reported(self, cutover: Any):
        facts = cutover.parse_firing(
            _firing(
                cutover,
                (0, "kalshi client mode=public budget=300/min base_url=https://x"),
                (1, "kalshi historical cap=9000 (300/min × 30 min, mode=public)"),
                (
                    2,
                    "kalshi historical cap reached during the archive walk pages=1500; cursor saved, the next run resumes it",
                ),
                (
                    3,
                    "kalshi pass finished outcome=ok duration=7200000 ms phases: catalog=ok candles=ok trades=ok historical=ok",
                ),
            )
        )
        checks = cutover.evaluate(
            facts,
            before=_status(remaining=8394, tape_from=None, walked=False),
            after=_status(remaining=8394, tape_from=None, walked=False),
            walk_firing=True,
        )
        texts = [t for _, t in checks]
        assert checks[0][0] is False and "mode=public" in texts[0]
        assert checks[1][0] is False and "cap=9000" in texts[1]
        assert any(ok and "capped after 1,500 pages" in t for ok, t in checks)
        assert any(
            ok and "walk firing duration 120.0 min (reported, not bounded)" in t
            for ok, t in checks
        )

    def test_render_report_marks_status(self, cutover: Any):
        facts = cutover.parse_firing(_firing(cutover, *JOURNAL_AFTER_WALK))
        checks = [(True, "fine")]
        report, all_ok = cutover.render_report(
            "v0.12.0",
            "abc123",
            "mt 0.12.0",
            [("Firing 1", facts, checks, "t")],
            _status(remaining=1, tape_from=None),
        )
        assert all_ok is True and "status: complete" in report
        assert "| 2026-06-30T23:00:00+00:00→2026-07-01T00:00:00+00:00 | 210 |" in report
        report, all_ok = cutover.render_report(
            "v0.12.0",
            "abc123",
            "mt 0.12.0",
            [("Firing 1", facts, [(False, "bad")], "t")],
            {},
        )
        assert (
            all_ok is False and "status: in_progress" in report and "❌ bad" in report
        )
