"""The 921 cutover script's control flow (slice 921, Task 7.4).

``scripts/`` is not a package: the module is loaded by path with the directory
on ``sys.path`` so it finds ``cutover_common`` — the pattern
``test_cutover_267.py`` established. Nothing here touches a host, a journal,
``sudo``, or a database; every seam that would is patched.

What these tests are FOR: the script runs with root privileges against
production, so its order of operations is the thing to pin. A precondition
that fails must abort before anything is written, and the happy path must
repair once, fire, and verify — not, say, fire before repairing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def cutover() -> Any:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "cutover_921_minute_sessions", SCRIPTS / "cutover_921_minute_sessions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_side_effects(cutover: Any, tmp_path: Path) -> Any:
    """No unit test may reach a real command (#22: an un-patched seam ran
    ``sudo mt-run minute`` from pytest), write the narration file into the
    repo, or sleep between verify polls."""
    import cutover_common

    def _forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"real subprocess in a unit test: {args[0]!r}")

    real_remaining_credits = cutover._remaining_credits
    with (
        patch.object(cutover_common.subprocess, "run", side_effect=_forbidden),
        patch.object(cutover_common, "_LOG_FILE", None),
        patch.object(cutover, "start_log", return_value=tmp_path / "cutover.log"),
        patch.object(cutover_common.time, "sleep"),
        patch.object(cutover.time, "sleep"),
        # The budget preflight reads the live EODHD balance; a unit test must
        # never depend on it (it did, and failed the day the balance dipped).
        patch.object(cutover, "_remaining_credits", return_value=500_000),
    ):
        # The one test of the real balance function reaches it here.
        cutover._remaining_credits_unpatched = real_remaining_credits
        yield


class _Firing:
    """Stand-in for cutover_common.Firing."""

    def __init__(self, matches: dict[Any, Any] | None = None) -> None:
        self.entries = [("ts", "line")]
        self._matches = matches or {}

    def first(self, pattern: Any) -> Any:
        return self._matches.get(pattern)


def _match(value: str) -> Any:
    m = MagicMock()
    m.group = MagicMock(return_value=value)
    return m


class TestPreconditionsAbortBeforeAnyWrite:
    def test_an_active_minute_pass_refuses(self, cutover: Any) -> None:
        from cutover_common import CutoverError

        repair = MagicMock()
        with (
            patch.object(cutover, "preflight", return_value="abc123"),
            patch.object(
                cutover, "unit_active", side_effect=lambda u: u == cutover.MINUTE_UNIT
            ),
            patch.object(cutover, "_repair", repair),
            patch.object(cutover, "install"),
        ):
            with pytest.raises(CutoverError, match="mt-minute-pass"):
                cutover.main(["--ref", "v0.14.0"])
        repair.assert_not_called()

    def test_an_active_daily_pass_refuses(self, cutover: Any) -> None:
        from cutover_common import CutoverError

        repair = MagicMock()
        with (
            patch.object(cutover, "preflight", return_value="abc123"),
            patch.object(
                cutover, "unit_active", side_effect=lambda u: u == cutover.DAILY_UNIT
            ),
            patch.object(cutover, "_repair", repair),
            patch.object(cutover, "install"),
        ):
            with pytest.raises(CutoverError, match="mt-daily-pass"):
                cutover.main(["--ref", "v0.14.0"])
        repair.assert_not_called()

    def test_a_bad_ref_refuses_before_the_repair(self, cutover: Any) -> None:
        from cutover_common import CutoverError

        repair = MagicMock()
        with (
            patch.object(cutover, "preflight", side_effect=CutoverError("unknown ref")),
            patch.object(cutover, "_repair", repair),
        ):
            with pytest.raises(CutoverError):
                cutover.main(["--ref", "nope"])
        repair.assert_not_called()


def _happy_path(cutover: Any, *, verify_text: str = "[PASS] all good"):
    """Patch every seam for a clean run; return the collected mocks."""
    repair_calls: list[str] = []

    def _fake_repair(mode: str) -> str:
        repair_calls.append(mode)
        return verify_text if mode == "--verify" else "counts"

    fire_calls: list[str] = []

    def _fake_fire(unit: str, args: list[str]):
        fire_calls.append(unit)
        return ("cursor", "started")

    install_mock = MagicMock()
    with (
        patch.object(cutover, "preflight", return_value="abc123"),
        patch.object(cutover, "unit_active", return_value=False),
        patch.object(cutover, "install", install_mock),
        patch.object(cutover, "_repair", side_effect=_fake_repair),
        patch.object(cutover, "fire_unit", side_effect=_fake_fire),
        patch.object(
            cutover,
            "fire_unit_until",
            side_effect=lambda unit, args, _p: (*_fake_fire(unit, args), True),
        ),
        patch.object(
            cutover,
            "read_unit_journal",
            return_value=_Firing({cutover.TRAILING_COMPLETE: _match("13083")}),
        ),
        patch.object(cutover, "unit_result", return_value=("success", "0")),
        patch.object(cutover, "run"),
    ):
        code = cutover.main(["--ref", "v0.14.0"])
    return code, repair_calls, fire_calls, install_mock


class TestHappyPath:
    def test_it_applies_the_repair_exactly_once(self, cutover: Any) -> None:
        _, repair_calls, _, _ = _happy_path(cutover)
        assert repair_calls.count("--apply") == 1

    def test_it_checks_before_and_after_the_repair(self, cutover: Any) -> None:
        _, repair_calls, _, _ = _happy_path(cutover)
        apply_at = repair_calls.index("--apply")
        assert "--check" in repair_calls[:apply_at]
        assert "--check" in repair_calls[apply_at + 1 :]

    def test_the_repair_precedes_every_firing(self, cutover: Any) -> None:
        """Firing before the repair would refetch the old truncated ranges and
        spend the allowance proving nothing."""
        calls: list[str] = []

        def _fake_repair(mode: str) -> str:
            calls.append(f"repair{mode}")
            return "[PASS]"

        def _fake_fire(unit: str, args: list[str]):
            calls.append(f"fire:{unit}")
            return ("cursor", "started")

        with (
            patch.object(cutover, "preflight", return_value="abc"),
            patch.object(cutover, "unit_active", return_value=False),
            patch.object(cutover, "install"),
            patch.object(cutover, "_repair", side_effect=_fake_repair),
            patch.object(cutover, "fire_unit", side_effect=_fake_fire),
            patch.object(
                cutover,
                "fire_unit_until",
                side_effect=lambda unit, args, _p: (*_fake_fire(unit, args), True),
            ),
            patch.object(
                cutover,
                "read_unit_journal",
                return_value=_Firing({cutover.TRAILING_COMPLETE: _match("1")}),
            ),
            patch.object(cutover, "unit_result", return_value=("success", "0")),
            patch.object(cutover, "run"),
        ):
            cutover.main(["--ref", "v0.14.0"])
        first_fire = next(i for i, c in enumerate(calls) if c.startswith("fire:"))
        assert "repair--apply" in calls[:first_fire]

    def test_it_fires_the_daily_pass_then_the_minute_pass(self, cutover: Any) -> None:
        _, _, fire_calls, _ = _happy_path(cutover)
        assert fire_calls[0] == cutover.DAILY_UNIT
        assert cutover.MINUTE_UNIT in fire_calls[1:]

    def test_it_ends_with_verify(self, cutover: Any) -> None:
        _, repair_calls, _, _ = _happy_path(cutover)
        assert repair_calls[-1] == "--verify"

    def test_a_clean_run_exits_zero(self, cutover: Any) -> None:
        code, _, _, _ = _happy_path(cutover)
        assert code == 0

    def test_skip_install_does_not_install(self, cutover: Any) -> None:
        install_mock = MagicMock()
        with (
            patch.object(cutover, "preflight", return_value="abc"),
            patch.object(cutover, "unit_active", return_value=False),
            patch.object(cutover, "install", install_mock),
            patch.object(cutover, "_repair", return_value="[PASS]"),
            patch.object(cutover, "fire_unit", return_value=("c", "s")),
            patch.object(cutover, "fire_unit_until", return_value=("c", "s", True)),
            patch.object(
                cutover,
                "read_unit_journal",
                return_value=_Firing({cutover.TRAILING_COMPLETE: _match("1")}),
            ),
            patch.object(cutover, "unit_result", return_value=("success", "0")),
            patch.object(cutover, "run"),
        ):
            cutover.main(["--ref", "v0.14.0", "--skip-install"])
        install_mock.assert_not_called()


class TestRepeatedMinuteFirings:
    """The trailing phase takes one chunk per symbol, so a symbol left with
    several in-window ranges needs several passes."""

    @staticmethod
    def _run_with(cutover: Any, verify_sequence: list[str], abort: str | None = None):
        verifies = iter(verify_sequence)
        fire_count = {"n": 0}

        def _fake_repair(mode: str) -> str:
            if mode == "--verify":
                return next(verifies, verify_sequence[-1])
            return "counts"

        def _fake_fire(unit: str, args: list[str]):
            if unit == cutover.MINUTE_UNIT:
                fire_count["n"] += 1
            return ("cursor", "started")

        matches = {cutover.TRAILING_COMPLETE: _match("13083")}
        if abort:
            matches[cutover.PASS_ABORTED] = _match(abort)

        with (
            patch.object(cutover, "preflight", return_value="abc"),
            patch.object(cutover, "unit_active", return_value=False),
            patch.object(cutover, "install"),
            patch.object(cutover, "_repair", side_effect=_fake_repair),
            patch.object(cutover, "fire_unit", side_effect=_fake_fire),
            patch.object(
                cutover,
                "fire_unit_until",
                side_effect=lambda unit, args, _p: (*_fake_fire(unit, args), True),
            ),
            patch.object(cutover, "read_unit_journal", return_value=_Firing(matches)),
            patch.object(cutover, "unit_result", return_value=("success", "0")),
            patch.object(cutover, "run"),
        ):
            code = cutover.main(["--ref", "v0.14.0"])
        return code, fire_count["n"]

    def test_it_stops_as_soon_as_verify_passes(self, cutover: Any) -> None:
        _, fires = self._run_with(cutover, ["[PASS] ok"])
        assert fires == 1

    def test_it_fires_again_while_verify_still_fails(self, cutover: Any) -> None:
        _, fires = self._run_with(
            cutover, ["[FAIL] pending", "[FAIL] pending", "[PASS] ok"]
        )
        assert fires == 3

    def test_a_quota_abort_ends_the_loop_and_is_not_retried(self, cutover: Any) -> None:
        """Retrying after QUOTA_EXHAUSTED spends nothing and learns nothing —
        the allowance is gone until the 00:00 UTC reset."""
        code, fires = self._run_with(
            cutover, ["[FAIL] pending"] * 5, abort="quota_exhausted"
        )
        assert fires == 1
        assert code == 1, "an aborted pass must not report a complete cutover"

    def test_the_firing_ceiling_is_bounded(self, cutover: Any) -> None:
        _, fires = self._run_with(cutover, ["[FAIL] pending"] * 20)
        assert fires == cutover.MAX_MINUTE_FIRINGS

    def test_a_failing_verify_exits_non_zero(self, cutover: Any) -> None:
        code, _ = self._run_with(cutover, ["[FAIL] still pending"] * 20)
        assert code == 1


class TestTimersAreHeldAndReleased:
    def test_active_timers_are_stopped_and_restarted(self, cutover: Any) -> None:
        run_mock = MagicMock()
        with (
            patch.object(cutover, "preflight", return_value="abc"),
            patch.object(
                cutover,
                "unit_active",
                side_effect=lambda u: u in (cutover.MINUTE_TIMER, cutover.DAILY_TIMER),
            ),
            patch.object(cutover, "install"),
            patch.object(cutover, "_repair", return_value="[PASS]"),
            patch.object(cutover, "fire_unit", return_value=("c", "s")),
            patch.object(cutover, "fire_unit_until", return_value=("c", "s", True)),
            patch.object(
                cutover,
                "read_unit_journal",
                return_value=_Firing({cutover.TRAILING_COMPLETE: _match("1")}),
            ),
            patch.object(cutover, "unit_result", return_value=("success", "0")),
            patch.object(cutover, "run", run_mock),
        ):
            cutover.main(["--ref", "v0.14.0"])
        commands = [
            " ".join(call.args[0]) for call in run_mock.call_args_list if call.args
        ]
        assert any("stop mt-minute-pass.timer" in c for c in commands)
        assert any("start mt-minute-pass.timer" in c for c in commands)

    def test_timers_are_released_even_when_the_run_fails(self, cutover: Any) -> None:
        """The finally block: a cutover that dies mid-way must not leave
        production with its acquisition timers stopped."""
        run_mock = MagicMock()
        with (
            patch.object(cutover, "preflight", return_value="abc"),
            patch.object(
                cutover, "unit_active", side_effect=lambda u: u == cutover.MINUTE_TIMER
            ),
            patch.object(cutover, "install"),
            patch.object(cutover, "_repair", side_effect=RuntimeError("boom")),
            patch.object(cutover, "run", run_mock),
        ):
            with pytest.raises(RuntimeError):
                cutover.main(["--ref", "v0.14.0"])
        commands = [
            " ".join(call.args[0]) for call in run_mock.call_args_list if call.args
        ]
        assert any("start mt-minute-pass.timer" in c for c in commands)


class TestBudgetGate:
    """The firings produce the acceptance numbers, so running them against a
    spent account measures the starvation rather than the fix.

    This replaced a "run just after 00:00 UTC" instruction in the docstring.
    A timing rule a human has to honour is not a check — it is a hope; and it
    was wrong whenever extra calls are in the account, where the daily reset
    does not matter at all.
    """

    @staticmethod
    def _run_with_credits(cutover: Any, remaining: int):
        repair = MagicMock(return_value="[PASS]")
        with (
            patch.object(cutover, "preflight", return_value="abc"),
            patch.object(cutover, "unit_active", return_value=False),
            patch.object(cutover, "_remaining_credits", return_value=remaining),
            patch.object(cutover, "install"),
            patch.object(cutover, "_repair", repair),
            patch.object(cutover, "fire_unit", return_value=("c", "s")),
            patch.object(cutover, "fire_unit_until", return_value=("c", "s", True)),
            patch.object(
                cutover,
                "read_unit_journal",
                return_value=_Firing({cutover.TRAILING_COMPLETE: _match("1")}),
            ),
            patch.object(cutover, "unit_result", return_value=("success", "0")),
            patch.object(cutover, "run"),
        ):
            try:
                code = cutover.main(["--ref", "v0.14.0"])
            except Exception as exc:  # noqa: BLE001 — the test inspects it
                return exc, repair
        return code, repair

    def test_it_refuses_below_the_floor_before_writing(self, cutover: Any) -> None:
        from cutover_common import CutoverError

        result, repair = self._run_with_credits(cutover, 40_000)
        assert isinstance(result, CutoverError)
        assert "40,000" in str(result)
        repair.assert_not_called()

    def test_the_refusal_names_both_ways_out(self, cutover: Any) -> None:
        result, _ = self._run_with_credits(cutover, 1_000)
        assert "00:00 UTC" in str(result)
        assert "extra calls" in str(result)

    def test_ample_credit_proceeds(self, cutover: Any) -> None:
        code, repair = self._run_with_credits(cutover, 593_142)
        assert code == 0
        assert repair.called

    def test_exactly_the_floor_proceeds(self, cutover: Any) -> None:
        code, _ = self._run_with_credits(cutover, cutover.MIN_CREDITS_TO_START)
        assert code == 0

    def test_the_floor_covers_a_full_trailing_phase(self, cutover: Any) -> None:
        """13,083 symbols x 1 chunk x 5 credits = 65,415 for the trailing
        phase alone; the floor must clear that with room for the daily pass."""
        from manta_trading.constants import EODHD_INTRADAY_CALL_COST

        trailing_cost = 13_083 * 1 * EODHD_INTRADAY_CALL_COST
        assert cutover.MIN_CREDITS_TO_START > trailing_cost

    def test_remaining_credits_counts_extras(self, cutover: Any) -> None:
        """extraLimit is the whole reason the 00:00 UTC rule can be dropped."""
        with patch.object(cutover, "httpx", create=True):
            pass
        import httpx

        response = MagicMock()
        response.json = MagicMock(
            return_value={
                "apiRequests": 6_858,
                "dailyRateLimit": 100_000,
                "extraLimit": 500_000,
            }
        )
        response.raise_for_status = MagicMock()
        with patch.object(httpx, "get", return_value=response):
            assert cutover._remaining_credits_unpatched() == 593_142


class TestBoundedFiringAndRecord:
    """#22: the first cutover's firing ran ten hours (backfill is unbounded)
    and its only record was terminal scrollback."""

    @staticmethod
    def _fire(
        cutover: Any, *, lines: list[Any], active: list[bool]
    ) -> tuple[bool, list[list[str]]]:
        import cutover_common

        stopped: list[list[str]] = []
        line_iter = iter(lines)
        active_iter = iter(active)

        started: list[list[str]] = []

        def _fake_run(args, **kwargs):
            if args[:2] == ["systemctl", "stop"]:
                stopped.append(args)
            if args[:2] == ["systemctl", "start"]:
                started.append(args)
            assert args[0] != "mt-run", "mt-run blocks until the unit ends"
            return MagicMock(stdout="")

        with (
            patch.object(cutover_common, "journal_cursor", return_value="cur"),
            patch.object(
                cutover_common,
                "journal_line_since",
                side_effect=lambda *a: next(line_iter),
            ),
            patch.object(
                cutover_common, "unit_active", side_effect=lambda _u: next(active_iter)
            ),
            patch.object(cutover_common, "run", side_effect=_fake_run),
        ):
            _cursor, _started, reached = cutover_common.fire_unit_until(
                "mt-minute-pass.service", ["minute"], cutover.TRAILING_COMPLETE
            )
        assert started == [
            ["systemctl", "start", "--no-block", "mt-minute-pass.service"]
        ]
        return reached, stopped

    def test_the_minute_firing_stops_once_the_trailing_phase_completes(
        self, cutover: Any
    ) -> None:
        # Unit stays active; the line appears on the second poll.
        reached, stopped = self._fire(
            cutover,
            lines=[None, _match("13083"), _match("13083")],
            active=[True, True, True, False, False],
        )
        assert reached is True
        assert stopped == [["systemctl", "stop", "mt-minute-pass.service"]]

    def test_a_firing_that_ends_without_the_line_is_not_stopped(
        self, cutover: Any
    ) -> None:
        reached, stopped = self._fire(cutover, lines=[None, None], active=[False])
        assert reached is False
        assert stopped == []

    def test_say_tees_to_the_log_file(self, tmp_path: Path) -> None:
        import cutover_common

        path = cutover_common.start_log(tmp_path / "logs" / "921.log")
        cutover_common.say("hello")
        cutover_common.say("world")
        text = path.read_text()
        assert "==> hello" in text and "==> world" in text

    def test_verify_is_repolled_while_the_cagg_is_stale(self, cutover: Any) -> None:
        answers = iter(["[WAIT] stale", "[WAIT] stale", "[PASS] fresh"])
        with patch.object(cutover, "_repair", side_effect=lambda _m: next(answers)):
            text = cutover._verify_when_fresh()
        assert text == "[PASS] fresh"

    def test_verify_wait_is_bounded(self, cutover: Any) -> None:
        with patch.object(cutover, "_repair", return_value="[WAIT] stale") as repair:
            text = cutover._verify_when_fresh()
        assert "[WAIT]" in text
        assert repair.call_count == cutover.VERIFY_WAIT_POLLS + 1

    def test_a_wait_verdict_never_counts_as_acceptance(self, cutover: Any) -> None:
        code, _repair_calls, _fires, _install = _happy_path(
            cutover, verify_text="[WAIT] stale"
        )
        assert code == 1


class TestStopTriggerSharesTheEmittersText:
    """#22 review F003: the cutover stops the firing on the trailing-complete
    line, so its pattern is built from the constant the daemon logs."""

    def test_the_pattern_matches_the_constant_as_emitted(self, cutover: Any) -> None:
        from manta_trading.constants import MINUTE_TRAILING_COMPLETE_LINE

        line = MINUTE_TRAILING_COMPLETE_LINE.format(count=13083)
        match = cutover.TRAILING_COMPLETE.search(f"... minute: {line}")
        assert match is not None and match.group(1) == "13083"

    def test_an_early_ended_phase_does_not_match(self, cutover: Any) -> None:
        assert (
            cutover.TRAILING_COMPLETE.search(
                "trailing phase ended early: quota_exhausted after 500 symbols"
            )
            is None
        )
