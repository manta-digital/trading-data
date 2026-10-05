"""Tests for the backup cluster table and its two parsers (slice 227, TD2).

The real ``deploy/backup-clusters.conf`` is the fixture: both parsers read it
field by field, and a parity test holds them to the same values. Malformed
tables are written to temp files and must fail in both parsers, naming the
line.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).parents[2]
_TABLE = _REPO_ROOT / "deploy" / "backup-clusters.conf"
_LIB = _REPO_ROOT / "deploy" / "lib" / "backup_clusters.sh"
_PRE227_CRON = _REPO_ROOT / "test" / "unit" / "fixtures" / "cron_main_pre227.txt"

# The expected rows, as the bash parser prints them ("-" kept as "-").
_EXPECTED = [
    (
        "17/main",
        "MT_TIMESCALE_MAINTENANCE_URL",
        "/data/backup",
        "-",
        "127.0.0.1",
        "0 2 * * *",
        "0 3 * * 0",
    ),
    (
        "17/tick",
        "MT_TICK_MAINTENANCE_URL",
        "/data/backup/17-tick",
        "17-tick",
        "-",
        "15 2 * * *",
        "30 2 * * 0",
    ),
]

_GOOD_ROW = "17/main MT_A /data/backup - 127.0.0.1 0 2 * * * 0 3 * * 0"

# name -> (table text, the line number the error must name)
_MALFORMED = {
    "short_row": ("17/main MT_A /data/backup - - 0 2 * * * 0 3 * *\n", 1),
    "long_row": ("17/main MT_A /data/backup - - 0 2 * * * 0 3 * * 0 x\n", 1),
    "duplicate": (f"{_GOOD_ROW}\n# c\n{_GOOD_ROW}\n", 3),
    "relative_root": ("17/main MT_A data/backup - - 0 2 * * * 0 3 * * 0\n", 1),
    "root_outside_data": ("17/main MT_A /srv/backup - - 0 2 * * * 0 3 * * 0\n", 1),
    "bare_data_root": ("17/main MT_A /data/ - - 0 2 * * * 0 3 * * 0\n", 1),
    "bad_url_key": ("17/main mt_a /data/backup - - 0 2 * * * 0 3 * * 0\n", 1),
    "shell_in_host": ("17/main MT_A /data/backup - h;rm 0 2 * * * 0 3 * * 0\n", 1),
    "space_escape_in_subpath": (
        "17/main MT_A /data/backup a$b - 0 2 * * * 0 3 * * 0\n",
        1,
    ),
    "dotdot_root": ("17/main MT_A /data/../etc/x - - 0 2 * * * 0 3 * * 0\n", 1),
    "bad_cron_field": ("17/main MT_A /data/backup - - 0 2 * * x 0 3 * * 0\n", 1),
}


@pytest.fixture(scope="module")
def bc() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "backup_clusters", _REPO_ROOT / "scripts" / "backup_clusters.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bash_print(table: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'. "{_LIB}" && backup_clusters_print "$1"', "_", str(table)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _bash_rows(table: Path) -> list[tuple[str, ...]]:
    result = _bash_print(table)
    assert result.returncode == 0, result.stderr
    return [tuple(line.split("\t")) for line in result.stdout.splitlines()]


def _py_rows(bc: ModuleType, table: Path) -> list[tuple[str, ...]]:
    return [
        (
            r.cluster,
            r.url_key,
            str(r.backup_root),
            r.remote_subpath or "-",
            r.replication_host or "-",
            r.metadata_cron,
            r.weekly_base_cron,
        )
        for r in bc.load_clusters(table)
    ]


def _whitespace_variant(tmp_path: Path) -> Path:
    """The real table with tabs, doubled spaces and trailing blanks."""
    lines = []
    for line in _TABLE.read_text().splitlines():
        if line and not line.startswith("#"):
            line = "\t".join(line.split(" ", 2)).replace(" ", "  ") + " \t "
        lines.append(line)
    out = tmp_path / "variant.conf"
    out.write_text("\n".join(lines))  # no trailing newline either
    return out


# --- the real table -----------------------------------------------------------


def test_bash_parses_real_table() -> None:
    assert _bash_rows(_TABLE) == _EXPECTED


def test_python_parses_real_table(bc: ModuleType) -> None:
    rows = bc.load_clusters(_TABLE)
    assert [r.cluster for r in rows] == ["17/main", "17/tick"]
    main, tick = rows
    assert main.remote_subpath is None and main.replication_host == "127.0.0.1"
    assert tick.remote_subpath == "17-tick" and tick.replication_host is None
    assert tick.backup_root == Path("/data/backup/17-tick")
    assert _py_rows(bc, _TABLE) == _EXPECTED


def test_parity_on_real_table(bc: ModuleType) -> None:
    assert _bash_rows(_TABLE) == _py_rows(bc, _TABLE)


def test_main_schedules_match_installed_cron() -> None:
    """Production's metadata and weekly times are today's installed ones."""
    lines = _PRE227_CRON.read_text().splitlines()
    main = _EXPECTED[0]
    (metadata,) = [x for x in lines if "cron_nightly_metadata.sh" in x]
    (weekly,) = [x for x in lines if "cron_weekly_backup.sh" in x]
    assert metadata.startswith(f"{main[5]} manta ")
    assert weekly.startswith(f"{main[6]} manta ")


def test_cluster_lookup(bc: ModuleType) -> None:
    assert bc.cluster(_TABLE, "17/tick").url_key == "MT_TICK_MAINTENANCE_URL"
    with pytest.raises(KeyError, match="17/nope"):
        bc.cluster(_TABLE, "17/nope")


def test_default_path_is_the_checked_in_table(bc: ModuleType) -> None:
    assert bc.TABLE_PATH == _TABLE


# --- whitespace variants -------------------------------------------------------


def test_bash_whitespace_variants(tmp_path: Path) -> None:
    assert _bash_rows(_whitespace_variant(tmp_path)) == _EXPECTED


def test_python_whitespace_variants(bc: ModuleType, tmp_path: Path) -> None:
    assert _py_rows(bc, _whitespace_variant(tmp_path)) == _EXPECTED


# --- malformed tables ----------------------------------------------------------


@pytest.mark.parametrize("case", sorted(_MALFORMED))
def test_bash_rejects_malformed(case: str, tmp_path: Path) -> None:
    text, line = _MALFORMED[case]
    table = tmp_path / "bad.conf"
    table.write_text(text)
    result = _bash_print(table)
    assert result.returncode != 0
    assert f"line {line}:" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("case", sorted(_MALFORMED))
def test_python_rejects_malformed(bc: ModuleType, case: str, tmp_path: Path) -> None:
    text, line = _MALFORMED[case]
    table = tmp_path / "bad.conf"
    table.write_text(text)
    with pytest.raises(ValueError, match=f"line {line}:"):
        bc.load_clusters(table)


def test_only_comments_rejected(bc: ModuleType, tmp_path: Path) -> None:
    table = tmp_path / "empty.conf"
    table.write_text("# nothing here\n\n   \n")
    result = _bash_print(table)
    assert result.returncode != 0 and "no rows" in result.stderr
    with pytest.raises(ValueError, match="no rows"):
        bc.load_clusters(table)


def test_sourcing_has_no_side_effects() -> None:
    result = subprocess.run(
        ["bash", "-c", f'. "{_LIB}"; declare -F | wc -l; echo "${{#BC_CLUSTER[@]}}"'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.split() == ["2", "0"]
