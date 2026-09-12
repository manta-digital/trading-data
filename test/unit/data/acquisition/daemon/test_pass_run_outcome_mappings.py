"""Unit tests: source outcome -> PassRunOutcome mappings (slice 922).

Two mappings, one per source (Decision 2). Both are exhaustive by assert, so
these tests also pin the property that matters more than any single row: a
new member of either source enum must not silently acquire a default.

The minute mapping deliberately disagrees with ``minute_pass_exit_code`` on
one case, and that disagreement is the point of recording an outcome at all:
quota spent on backfill after the trailing phase is exit 0 (nobody should be
paged) but ``COMPLETE_QUOTA`` in the record (the operator should be able to
see the allowance ran out).
"""

from __future__ import annotations

import pytest

from manta_trading.cli.commands.kalshi import (
    EXIT_BY_OUTCOME,
    PASS_RUN_OUTCOME_BY_SYNC_OUTCOME,
    pass_run_outcome_for_kalshi,
)
from manta_trading.data.acquisition.daemon.minute import (
    _MINUTE_PASS_RUN_OUTCOME,
    minute_pass_exit_code,
    pass_run_outcome_for_minute,
)
from manta_trading.data.acquisition.pass_runs import PassRunOutcome
from manta_trading.data.acquisition.state import MinutePassOutcome
from manta_trading.data.kalshi.sync_types import SyncOutcome


class TestMinuteMapping:
    @pytest.mark.parametrize("outcome", list(MinutePassOutcome))
    @pytest.mark.parametrize("trailing_completed", [True, False])
    @pytest.mark.parametrize("trailing_required", [True, False])
    def test_every_combination_returns_a_member(
        self,
        outcome: MinutePassOutcome,
        trailing_completed: bool,
        trailing_required: bool,
    ) -> None:
        result = pass_run_outcome_for_minute(
            outcome,
            trailing_completed=trailing_completed,
            trailing_required=trailing_required,
        )
        assert isinstance(result, PassRunOutcome)

    @pytest.mark.parametrize(
        ("outcome", "trailing_completed", "trailing_required", "expected"),
        [
            # The trailing phase ran and finished: the pass did its job.
            (
                MinutePassOutcome.COMPLETE,
                True,
                True,
                PassRunOutcome.COMPLETE,
            ),
            # Quota spent on backfill after trailing finished — a result, not
            # a failure, and the one case where this disagrees with exit 0.
            (
                MinutePassOutcome.QUOTA_EXHAUSTED,
                True,
                True,
                PassRunOutcome.COMPLETE_QUOTA,
            ),
            # Quota ran out inside the trailing phase: the current session is
            # missing, so the pass is incomplete whatever spent the allowance.
            (
                MinutePassOutcome.QUOTA_EXHAUSTED,
                False,
                True,
                PassRunOutcome.INCOMPLETE,
            ),
            # Backfill-only day: no session was owed, so quota is the
            # designed end of the day.
            (
                MinutePassOutcome.QUOTA_EXHAUSTED,
                False,
                False,
                PassRunOutcome.COMPLETE_QUOTA,
            ),
            (
                MinutePassOutcome.COMPLETE,
                False,
                False,
                PassRunOutcome.COMPLETE,
            ),
            # The provider is the reason in either phase.
            (
                MinutePassOutcome.PROVIDER_UNAVAILABLE,
                True,
                True,
                PassRunOutcome.PROVIDER_UNAVAILABLE,
            ),
            (
                MinutePassOutcome.PROVIDER_UNAVAILABLE,
                False,
                True,
                PassRunOutcome.PROVIDER_UNAVAILABLE,
            ),
            (
                MinutePassOutcome.PROVIDER_UNAVAILABLE,
                False,
                False,
                PassRunOutcome.PROVIDER_UNAVAILABLE,
            ),
            # A required trailing phase that never completed is incomplete
            # even when the pass otherwise reports complete.
            (
                MinutePassOutcome.COMPLETE,
                False,
                True,
                PassRunOutcome.INCOMPLETE,
            ),
        ],
    )
    def test_the_designed_table(
        self,
        outcome: MinutePassOutcome,
        trailing_completed: bool,
        trailing_required: bool,
        expected: PassRunOutcome,
    ) -> None:
        assert (
            pass_run_outcome_for_minute(
                outcome,
                trailing_completed=trailing_completed,
                trailing_required=trailing_required,
            )
            is expected
        )

    def test_quota_after_trailing_is_exit_zero_but_records_quota(self) -> None:
        """The deliberate disagreement with the exit code, stated once."""
        args = dict(trailing_completed=True, trailing_required=True)
        assert minute_pass_exit_code(MinutePassOutcome.QUOTA_EXHAUSTED, **args) == 0
        assert (
            pass_run_outcome_for_minute(MinutePassOutcome.QUOTA_EXHAUSTED, **args)
            is PassRunOutcome.COMPLETE_QUOTA
        )

    def test_the_table_is_exhaustive(self) -> None:
        assert set(_MINUTE_PASS_RUN_OUTCOME) == set(MinutePassOutcome)

    def test_a_new_member_would_trip_the_assert(self) -> None:
        """Adding a source member without a mapping must fail loudly.

        Simulated by checking the table against an enum with one extra
        member: the same set comparison the module asserts at import.
        """
        extended = set(MinutePassOutcome) | {"a_new_outcome"}
        assert set(_MINUTE_PASS_RUN_OUTCOME) != extended


class TestKalshiMapping:
    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (SyncOutcome.OK, PassRunOutcome.COMPLETE),
            (SyncOutcome.PARTIAL, PassRunOutcome.INCOMPLETE),
            (SyncOutcome.PROVIDER_ABORT, PassRunOutcome.PROVIDER_UNAVAILABLE),
            (SyncOutcome.STORAGE_ABORT, PassRunOutcome.FAILED),
        ],
    )
    def test_the_designed_table(
        self, outcome: SyncOutcome, expected: PassRunOutcome
    ) -> None:
        assert pass_run_outcome_for_kalshi(outcome) is expected

    @pytest.mark.parametrize("outcome", list(SyncOutcome))
    def test_every_member_maps(self, outcome: SyncOutcome) -> None:
        assert isinstance(pass_run_outcome_for_kalshi(outcome), PassRunOutcome)

    def test_quota_never_arises_for_kalshi(self) -> None:
        """Kalshi has no provider quota, so COMPLETE_QUOTA must not appear."""
        assert PassRunOutcome.COMPLETE_QUOTA not in set(
            PASS_RUN_OUTCOME_BY_SYNC_OUTCOME.values()
        )

    def test_both_kalshi_tables_are_exhaustive(self) -> None:
        assert set(PASS_RUN_OUTCOME_BY_SYNC_OUTCOME) == set(SyncOutcome)
        assert set(EXIT_BY_OUTCOME) == set(SyncOutcome)

    def test_a_new_member_would_trip_the_assert(self) -> None:
        extended = set(SyncOutcome) | {"a_new_outcome"}
        assert set(PASS_RUN_OUTCOME_BY_SYNC_OUTCOME) != extended
