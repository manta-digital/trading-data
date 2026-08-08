"""Target selection for ``scripts/run_tests.py`` (slice 913, task 2.6).

The runner used to pass ``test/{tier}`` unconditionally, so naming a file added
a *second* target rather than narrowing the run — pytest executed the whole
tier and the caller's intent was silently discarded. Two full-tier runs went by
before that was noticed, which is the cost of a flag that looks like it worked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from run_tests import build_pytest_args  # noqa: E402


def test_no_target_runs_the_whole_tier() -> None:
    assert build_pytest_args("integration", []) == ["test/integration"]


def test_flags_alone_still_run_the_whole_tier() -> None:
    """A bare flag is not a target; the tier directory must still be added."""
    assert build_pytest_args("unit", ["-q", "-x"]) == ["test/unit", "-q", "-x"]


def test_explicit_file_replaces_the_tier_directory() -> None:
    """The regression this task fixes: the tier dir must NOT also be passed."""
    args = build_pytest_args("integration", ["test/integration/data/test_x.py", "-q"])
    assert args == ["test/integration/data/test_x.py", "-q"]
    assert "test/integration" not in args


def test_explicit_nodeid_replaces_the_tier_directory() -> None:
    """``file::class::test`` is a target too — split on '::' before matching."""
    nodeid = "test/integration/test_x.py::TestC::test_m"
    assert build_pytest_args("integration", [nodeid]) == [nodeid]


def test_target_from_another_tier_does_not_suppress_the_tier_dir() -> None:
    """A path outside the tier is not a valid narrowing.

    Otherwise ``run_tests.py unit test/integration/foo.py`` would run
    integration files under the *unit* tier's environment allowlist, quietly
    widening what those tests can reach.
    """
    args = build_pytest_args("unit", ["test/integration/foo.py"])
    assert args == ["test/unit", "test/integration/foo.py"]


def test_flag_value_resembling_a_path_is_not_treated_as_a_target() -> None:
    """``-k`` and friends start with '-' and must never count as targets."""
    args = build_pytest_args("integration", ["-k", "test_role"])
    assert args == ["test/integration", "-k", "test_role"]


@pytest.mark.parametrize("tier", ["unit", "integration", "load"])
def test_every_tier_defaults_to_its_own_directory(tier: str) -> None:
    assert build_pytest_args(tier, []) == [f"test/{tier}"]
