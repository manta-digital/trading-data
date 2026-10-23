"""Tests for the backup rows of ``mt data health`` (slice 924).

Flag fixtures under ``test/unit/fixtures/backup_flags/`` are laid out exactly
as ``backup_health_cron.sh`` writes them: title, ``detected:``, then the whole
``check_backup_health.sh`` output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from manta_trading.cli.commands.health_backup import (
    NOT_SET,
    RootObservation,
    check_backup_root,
    parse_backup_roots,
    summarise_flag,
)
from manta_trading.constants import (
    BACKUP_ARCHIVE_FLAG,
    BACKUP_STALE_FLAG,
    HEALTH_BACKUP_CHECKER_QUIET_AFTER,
)

_FLAGS = Path(__file__).parents[2] / "fixtures" / "backup_flags"
NOW = datetime(2026, 10, 18, 12, 50, tzinfo=UTC)
LIMIT = HEALTH_BACKUP_CHECKER_QUIET_AFTER


def _fixture(name: str) -> str:
    return (_FLAGS / name).read_text()


class TestParseBackupRoots:
    def test_two_roots(self) -> None:
        assert parse_backup_roots("/data/backup,/data/backup/17-tick") == [
            "/data/backup",
            "/data/backup/17-tick",
        ]

    def test_spaces_and_trailing_comma(self) -> None:
        assert parse_backup_roots(" /a , /b ,") == ["/a", "/b"]

    def test_duplicates_collapse_in_order(self) -> None:
        assert parse_backup_roots("/b,/a,/b") == ["/b", "/a"]

    @pytest.mark.parametrize("raw", [None, "", " , "])
    def test_unset_or_blank(self, raw: str | None) -> None:
        assert parse_backup_roots(raw) == NOT_SET

    @pytest.mark.parametrize("bad", ["relative/path", "/data/../etc"])
    def test_invalid_entry_named(self, bad: str) -> None:
        result = parse_backup_roots(f"/data/backup,{bad}")
        assert isinstance(result, str)
        assert repr(bad) in result


class TestSummariseFlag:
    def test_archive_body_2026_10_18(self) -> None:
        text = summarise_flag(
            BACKUP_ARCHIVE_FLAG, _fixture("archive_broken_2026-10-18.txt")
        )
        assert text.startswith("ARCHIVE-BROKEN since 2026-10-18T06:00:01-06:00: ")
        assert "archiver_failing: the most recent archive attempt failed" in text
        assert "unarchived_backlog: 24454157880 bytes" in text
        assert "threshold 4294967296" in text
        assert "archive_tmp_leftover: older than 10 min" in text
        # The stale failure belongs to the other flag (FAILED line's classes).
        assert "offsite_wal_stale" not in text

    def test_stale_body_names_only_its_class(self) -> None:
        text = summarise_flag(
            BACKUP_STALE_FLAG, _fixture("backup_stale_2026-10-18.txt")
        )
        assert text == (
            "BACKUP-STALE since 2026-10-18T06:00:01-06:00: offsite_wal_stale: "
            "push stamp /data/backup/wal-offsite.stamp is 2564 min old "
            "(limit 180 min)"
        )

    def test_wedged_body_2026_09_06(self) -> None:
        text = summarise_flag(
            BACKUP_ARCHIVE_FLAG, _fixture("archive_wedged_2026-09-06.txt")
        )
        assert text.startswith("ARCHIVE-BROKEN since 2026-09-06T13:30:01-06:00: ")
        assert "archive_wedged: /data/backup/wal/0000000100001244000000FA" in text
        assert "PASS" not in text

    def test_crlf_and_leading_spaces(self) -> None:
        body = "title\r\n  detected: T1\r\n  FAIL a_check: broke\r\n"
        assert summarise_flag("X", body) == "X since T1: a_check: broke"

    def test_detected_not_on_line_two(self) -> None:
        body = "title\nFAIL a: one\ndetected: T2\n"
        assert summarise_flag("X", body) == "X since T2: a: one"

    def test_no_detected_line(self) -> None:
        assert summarise_flag("X", "FAIL a: one\nFAIL b: two\n") == (
            "X (no detected time): a: one; b: two"
        )

    def test_no_fail_line_shows_first_line(self) -> None:
        assert summarise_flag("X", "\n  someone touched this\n") == (
            "X (no detected time): someone touched this"
        )

    def test_empty_file(self) -> None:
        assert summarise_flag("X", "") == "X (no detected time): (empty)"

    def test_cannot_check_has_no_failed_line(self) -> None:
        body = "t\ndetected: T\nFAIL cannot_check: MT_URL not found in /e\n"
        assert summarise_flag(BACKUP_ARCHIVE_FLAG, body) == (
            "ARCHIVE-BROKEN since T: cannot_check: MT_URL not found in /e"
        )


def _obs(**kwargs: object) -> RootObservation:
    fields: dict[str, object] = {"root": "/r", "log_mtime": NOW - timedelta(minutes=18)}
    fields.update(kwargs)
    return RootObservation(**fields)  # type: ignore[arg-type]


class TestCheckBackupRoot:
    def test_healthy(self) -> None:
        check = check_backup_root(_obs(), NOW, LIMIT)
        assert check.ok
        assert check.name == "backup /r"
        assert check.detail == "no flags; checker ran 0.3 h ago; limit 2 h"
        assert check.counted

    def test_archive_flag_only(self) -> None:
        check = check_backup_root(_obs(archive_flag="FAIL a: x"), NOW, LIMIT)
        assert not check.ok
        assert check.detail == "ARCHIVE-BROKEN (no detected time): a: x"

    def test_stale_flag_only(self) -> None:
        check = check_backup_root(_obs(stale_flag="FAIL s: y"), NOW, LIMIT)
        assert not check.ok
        assert check.detail == "BACKUP-STALE (no detected time): s: y"

    def test_both_flags_archive_first(self) -> None:
        check = check_backup_root(
            _obs(archive_flag="FAIL a: x", stale_flag="FAIL s: y"), NOW, LIMIT
        )
        assert check.detail == (
            "ARCHIVE-BROKEN (no detected time): a: x | "
            "BACKUP-STALE (no detected time): s: y"
        )

    def test_both_flags_and_quiet_checker(self) -> None:
        check = check_backup_root(
            _obs(
                archive_flag="FAIL a: x",
                stale_flag="FAIL s: y",
                log_mtime=NOW - timedelta(hours=3),
            ),
            NOW,
            LIMIT,
        )
        problems = check.detail.split(" | ")
        assert len(problems) == 3
        assert problems[0].startswith("ARCHIVE-BROKEN")
        assert problems[1].startswith("BACKUP-STALE")
        assert problems[2] == (
            "checker last ran 3.0 h ago; limit 2 h "
            "— backup_health_cron.sh is not running"
        )

    def test_log_absent(self) -> None:
        check = check_backup_root(_obs(log_mtime=None), NOW, LIMIT)
        assert not check.ok
        assert check.detail == (
            "no backup-health.log — backup_health_cron.sh has never run here"
        )

    def test_log_exactly_at_threshold_is_healthy(self) -> None:
        assert check_backup_root(_obs(log_mtime=NOW - LIMIT), NOW, LIMIT).ok

    def test_log_one_second_past_threshold_is_quiet(self) -> None:
        late = NOW - LIMIT - timedelta(seconds=1)
        check = check_backup_root(_obs(log_mtime=late), NOW, LIMIT)
        assert not check.ok
        assert "backup_health_cron.sh is not running" in check.detail

    def test_unreadable_root_skips_the_rest(self) -> None:
        check = check_backup_root(
            _obs(read_error="cannot read /r: Permission denied", log_mtime=None),
            NOW,
            LIMIT,
        )
        assert not check.ok
        assert check.detail == "cannot read /r: Permission denied"

    def test_2026_10_18_shape(self) -> None:
        check = check_backup_root(
            _obs(
                root="/data/backup",
                archive_flag=_fixture("archive_broken_2026-10-18.txt"),
                stale_flag=_fixture("backup_stale_2026-10-18.txt"),
            ),
            NOW,
            LIMIT,
        )
        assert not check.ok
        assert check.detail.startswith("ARCHIVE-BROKEN since 2026-10-18T06:00")
        assert " | BACKUP-STALE since" in check.detail
