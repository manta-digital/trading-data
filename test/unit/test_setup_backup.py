"""Tests for ``deploy/setup-backup.sh`` and its libraries (slice 920, Section 3).

No root, no database, no network. The orchestrator is exercised in
``--check`` mode with ``--rehearse`` (so nothing under /etc is touched) and
with the host tools it consults stubbed on ``PATH``; each library is
exercised directly in both check and apply mode, with ``psql``/``restic``
stubs that record what they were asked to do.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[2]
_DEPLOY = _REPO_ROOT / "deploy"
_SETUP = _DEPLOY / "setup-backup.sh"
_LIB = _DEPLOY / "lib"
_TEMPLATE = _DEPLOY / "cron.d" / "manta-trading-backup"

_REQUIRED = ("--checkout", "--env-file", "--backup-root")

# The live /etc/timeshift/timeshift.json shape on manta9000 (2026-09-05):
# every scalar a JSON string, `"key" : value` spacing, count_weekly 3.
_LIVE_TIMESHIFT = """{
  "backup_device_uuid" : "277accd4-f2ef-4814-af94-f127b06eaf4b",
  "parent_device_uuid" : "",
  "do_first_run" : "false",
  "btrfs_mode" : "false",
  "include_btrfs_home_for_backup" : "false",
  "include_btrfs_home_for_restore" : "false",
  "stop_cron_emails" : "true",
  "schedule_monthly" : "false",
  "schedule_weekly" : "true",
  "schedule_daily" : "false",
  "schedule_hourly" : "false",
  "schedule_boot" : "false",
  "count_monthly" : "2",
  "count_weekly" : "3",
  "count_daily" : "5",
  "count_hourly" : "6",
  "count_boot" : "5",
  "snapshot_size" : "35387754864",
  "snapshot_count" : "421548",
  "date_format" : "%Y-%m-%d %H:%M:%S",
  "exclude" : [
    "/home/manta/**",
    "/var/lib/libvirt/**",
    "/var/lib/postgresql/**",
    "/root/**"
  ],
  "exclude-apps" : []
}
"""
_MANAGED_KEYS = (
    "schedule_monthly",
    "schedule_weekly",
    "schedule_daily",
    "schedule_hourly",
    "schedule_boot",
    "count_weekly",
    "exclude",
)
_EXCLUDES = (
    "/home/manta/**",
    "/var/lib/postgresql/**",
    "/var/lib/libvirt/**",
    "/root/**",
)


def _stub(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def _run(
    cmd: list[str], bin_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)


def _env_file(path: Path, password: bool = True) -> Path:
    lines = [
        'MT_BACKUP_S3_ENDPOINT="https://s3.example.invalid"',
        'MT_BACKUP_S3_KEY_ID="keyid"',
        'MT_BACKUP_S3_APPLICATION_KEY="appkey"',
        'MT_BACKUP_S3_BUCKET="bucket-x"',
    ]
    if password:
        lines.append('MT_BACKUP_RESTIC_PASSWORD="pw"')
    path.write_text("\n".join(lines) + "\n")
    return path


# --- setup-backup.sh -----------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path) -> dict[str, Path]:
    """A checkout, env file, scratch backup root, rehearse dir, and stubs."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "dpkg", "exit 1\n")  # restic not installed
    _stub(bin_dir, "crontab", "exit 1\n")  # unreadable, as for a non-root run
    _stub(bin_dir, "restic", "exit 1\n")
    _stub(bin_dir, "pg_lsclusters", _PG_LSCLUSTERS_STUB)
    rules = tmp_path / "lifecycle.json"
    rules.write_text(_lifecycle_json("base/", "wal/"))
    _stub(bin_dir, "rclone", f'[ "$2" = lifecycle ] && cat {str(rules)!r}\n')
    return {
        "checkout": checkout,
        "env": _env_file(tmp_path / "env", password=False),
        "root": tmp_path / "backup-root",
        "rehearse": tmp_path / "rehearse",
        "bin": bin_dir,
        "rules": rules,
    }


# The real tool's output (manta9000, 2026-10-04), header included: the stub
# honours --no-header the way pg_lsclusters does.
_PG_LSCLUSTERS = """\
Ver Cluster Port Status Owner     Data directory              Log file
17  main    5432 online postgres  /var/lib/postgresql/17/main /var/log/postgresql/postgresql-17-main.log
17  tick    5433 online <unknown> /data/postgresql/17/tick    /var/log/postgresql/postgresql-17-tick.log
"""  # noqa: E501
_PG_LSCLUSTERS_STUB = (
    'case "${1:-}" in -h|--no-header) skip=2 ;; *) skip=1 ;; esac\n'
    f"tail -n +$skip <<'X'\n{_PG_LSCLUSTERS}X\n"
)


def _lifecycle_json(*prefixes: str, days: int = 30) -> str:
    return json.dumps(
        [
            {
                "daysFromHidingToDeleting": days,
                "daysFromUploadingToHiding": None,
                "daysFromStartingToCancelingUnfinishedLargeFiles": None,
                "fileNamePrefix": p,
            }
            for p in prefixes
        ]
    )


def _check(host: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_SETUP),
            "--checkout",
            str(host["checkout"]),
            "--env-file",
            str(host["env"]),
            "--backup-root",
            str(host["root"]),
            "--check",
            "--rehearse",
            str(host["rehearse"]),
            *extra,
        ],  # fmt: skip
        host["bin"],
    )


class TestSetupArguments:
    @pytest.mark.parametrize("missing", _REQUIRED)
    def test_each_required_argument_named_when_missing(
        self, host: dict[str, Path], missing: str
    ) -> None:
        values = {
            "--checkout": str(host["checkout"]),
            "--env-file": str(host["env"]),
            "--backup-root": str(host["root"]),
        }
        args = [a for k, v in values.items() if k != missing for a in (k, v)]
        result = _run([str(_SETUP), *args, "--check"], host["bin"])
        assert result.returncode == 2
        assert missing in result.stderr

    def test_unknown_argument_refused(self, host: dict[str, Path]) -> None:
        result = _check(host, "--frobnicate")
        assert result.returncode == 2
        assert "unknown argument" in result.stderr

    def test_apply_mode_refused_when_not_root(self, host: dict[str, Path]) -> None:
        assert os.geteuid() != 0, "this test must not run as root"
        result = _run(
            [
                str(_SETUP),
                "--checkout",
                str(host["checkout"]),
                "--env-file",
                str(host["env"]),
                "--backup-root",
                str(host["root"]),
            ],  # fmt: skip
            host["bin"],
        )
        assert result.returncode == 1
        assert "sudo" in result.stderr
        assert not host["root"].exists(), "refusal must happen before any action"


class TestSetupCheckMode:
    def test_scratch_root_reports_missing_and_never_applies(
        self, host: dict[str, Path]
    ) -> None:
        result = _check(host)
        assert result.returncode == 1, result.stdout + result.stderr
        out = result.stdout
        assert "APPLIED" not in out
        assert "MISSING package-restic" in out
        assert "MISSING dir-system" in out
        for cluster in ("17/main", "17/tick"):
            for item in ("dir-root", "dir-base", "dir-wal", "dir-metadata"):
                assert f"MISSING {cluster} {item} " in out, out
            assert f"MISSING {cluster} wal-acl-manta" in out
            assert f"SKIPPED {cluster} pg-settings (rehearse)" in out
            assert f"MISSING {cluster} arm-file" in out
        assert "MISSING cron.d" in out
        assert "MISSING timeshift-config" in out
        assert "MISSING restic-password" in out
        assert "MISSING user-crontab" in out
        assert out.rstrip().splitlines()[-1].startswith("SUMMARY applied=0 not-ok=")
        assert not host["root"].exists(), "--check must create nothing"
        assert not (host["rehearse"] / "clusters").exists()
        assert not (host["rehearse"] / "cron.d").exists()

    def test_rehearse_moves_every_cluster_root(self, host: dict[str, Path]) -> None:
        out = _check(host).stdout
        rehearse = host["rehearse"] / "clusters"
        assert f"MISSING 17/main dir-root {rehearse}/data/backup\n" in out
        assert f"MISSING 17/tick dir-wal {rehearse}/data/backup/17-tick/wal\n" in out
        assert " /data/backup" not in out.replace(f"{rehearse}/data", "")

    def test_host_items_reported_once_without_prefix(
        self, host: dict[str, Path]
    ) -> None:
        lines = _check(host).stdout.splitlines()
        for item in ("package-restic", "dir-system", "cron.d", "user-crontab"):
            hits = [x for x in lines if x.split()[1:2] == [item]]
            assert len(hits) == 1, (item, hits)
        assert not any(" 17/" in x for x in lines if "restic-" in x)

    def test_cluster_dir_shape_drift(self, host: dict[str, Path]) -> None:
        """A cron-written directory must be the cron user's and 775."""
        base = host["rehearse"] / "clusters" / "data" / "backup" / "17-tick" / "base"
        base.mkdir(parents=True, mode=0o700)
        base.chmod(0o700)
        out = _check(host).stdout
        assert "OK 17/tick dir-base\n" in out
        assert "DRIFT 17/tick dir-base-owner-mode 'manta:manta 775' " in out
        assert base.stat().st_mode & 0o777 == 0o700, "--check must change nothing"

    def test_malformed_table_stops_before_any_step(
        self, host: dict[str, Path], tmp_path: Path
    ) -> None:
        """The table is parsed before step 1: a bad row prints no step header.
        Run against a copy of deploy/ whose table is broken."""
        deploy = tmp_path / "deploy"
        subprocess.run(["cp", "-r", str(_DEPLOY), str(deploy)], check=True)
        table = deploy / "backup-clusters.conf"
        table.write_text(table.read_text() + "17/x MT_A /data/x - -\n")
        result = _run(
            [
                str(deploy / "setup-backup.sh"),
                "--checkout",
                str(host["checkout"]),
                "--env-file",
                str(host["env"]),
                "--backup-root",
                str(host["root"]),
                "--check",
                "--rehearse",
                str(host["rehearse"]),
            ],  # fmt: skip
            host["bin"],
        )
        assert result.returncode == 1
        assert "expected 15 fields" in result.stderr
        assert "==>" not in result.stdout
        assert not host["rehearse"].exists()

    def test_cluster_absent_from_pg_lsclusters_is_fatal(
        self, host: dict[str, Path]
    ) -> None:
        main_only = _PG_LSCLUSTERS.splitlines()[1]
        _stub(host["bin"], "pg_lsclusters", f"cat <<'X'\n{main_only}\nX\n")
        result = _check(host)
        assert result.returncode == 1
        assert "cluster 17/tick is in" in result.stderr
        assert "not in pg_lsclusters (nothing changed)" in result.stderr
        assert "==>" not in result.stdout

    def test_data_directory_comes_from_pg_lsclusters(
        self, host: dict[str, Path]
    ) -> None:
        out = _check(host).stdout
        assert "cluster 17/tick (root " in out
        assert "data /data/postgresql/17/tick)" in out
        assert "data /var/lib/postgresql/17/main)" in out


class TestSetupLifecycle:
    """TD10: a missing rule is reported, never applied, never a failure."""

    def _lifecycle_lines(self, out: str) -> list[str]:
        return [x for x in out.splitlines() if " lifecycle" in x]

    def test_production_rules_only_gives_missing_tick(
        self, host: dict[str, Path]
    ) -> None:
        lines = self._lifecycle_lines(_check(host).stdout)
        assert "OK 17/main lifecycle base/" in lines
        assert "OK 17/main lifecycle wal/" in lines
        assert any(
            x.startswith("MISSING 17/tick lifecycle 17-tick/base/ (") for x in lines
        )
        assert any(
            x.startswith("MISSING 17/tick lifecycle 17-tick/wal/ (") for x in lines
        )

    def test_both_rule_sets_ok(self, host: dict[str, Path]) -> None:
        host["rules"].write_text(
            _lifecycle_json("base/", "wal/", "17-tick/base/", "17-tick/wal/")
        )
        lines = self._lifecycle_lines(_check(host).stdout)
        assert all(x.startswith("OK ") for x in lines) and len(lines) == 4

    def test_wrong_days_is_missing(self, host: dict[str, Path]) -> None:
        host["rules"].write_text(_lifecycle_json("base/", "wal/", days=7))
        lines = self._lifecycle_lines(_check(host).stdout)
        assert "MISSING 17/main lifecycle base/" in " ".join(lines)

    def test_missing_rule_does_not_change_the_exit_code(
        self, host: dict[str, Path]
    ) -> None:
        """The same run with and without tick's rules tallies the same."""

        def not_ok() -> str:
            return _check(host).stdout.rstrip().splitlines()[-1]

        without = not_ok()
        host["rules"].write_text(
            _lifecycle_json("base/", "wal/", "17-tick/base/", "17-tick/wal/")
        )
        assert not_ok() == without

    def test_unreadable_rules_are_a_failure(self, host: dict[str, Path]) -> None:
        _stub(host["bin"], "rclone", "echo 'unauthorized' >&2; exit 1\n")
        out = _check(host).stdout
        assert "MISSING 17/tick lifecycle-read (rclone backend lifecycle" in out
        assert "MISSING 17/main lifecycle-read" in out

    def test_never_sets_a_rule(self, host: dict[str, Path], tmp_path: Path) -> None:
        log = tmp_path / "rclone.log"
        _stub(
            host["bin"],
            "rclone",
            f'echo "$*" >> {str(log)!r}; cat {str(host["rules"])!r}\n',
        )
        _check(host)
        calls = log.read_text().splitlines()
        assert calls and all(c.split()[:2] == ["backend", "lifecycle"] for c in calls)
        assert not any(" -o " in f" {c} " for c in calls)

    def test_arm_file_present_is_ok_and_never_created(
        self, host: dict[str, Path]
    ) -> None:
        main_root = host["rehearse"] / "clusters" / "data" / "backup"
        main_root.mkdir(parents=True)
        (main_root / "RECONCILE-ARMED").touch()
        result = _check(host)
        assert f"OK 17/main arm-file {main_root}/RECONCILE-ARMED" in result.stdout
        assert "MISSING 17/tick arm-file" in result.stdout
        assert not (main_root / "17-tick" / "RECONCILE-ARMED").exists()

    def test_restic_prefix_override_reaches_cron_d_render(
        self, host: dict[str, Path]
    ) -> None:
        # The rendered cron.d (via --rehearse) must carry the scratch prefix.
        host["rehearse"].mkdir()
        (host["rehearse"] / "cron.d").write_text("stale\n")
        result = _check(host, "--restic-prefix", "system-accept")
        assert "DRIFT cron.d" in result.stdout
        assert "--repo-prefix system-accept " in subprocess.run(
            [
                str(_LIB / "render_cron.sh"),
                "--template", str(_TEMPLATE),
                "--cluster-template", str(_CLUSTER_TEMPLATE),
                "--clusters", str(_TABLE), "--interval", "60",
                "--checkout", str(host["checkout"]), "--env-file", str(host["env"]),
                "--host-root", str(host["root"]), "--cron-user", "manta",
                "--pgdata", "17/main=/x", "--pgdata", "17/tick=/y",
                "--keep-days", "7", "--remote-prefix", "b2:bucket-x",
                "--restic-prefix", "system-accept",
            ],
            capture_output=True, text=True,
        ).stdout  # fmt: skip

    def test_leftover_crontab_line_is_drift(self, host: dict[str, Path]) -> None:
        _stub(
            host["bin"],
            "crontab",
            "echo '0 3 * * 0 /x/scripts/cron_weekly_base.sh --env-file /x/.env'\n",
        )
        result = _check(host)
        assert "DRIFT user-crontab still runs: cron_weekly_base.sh" in result.stdout

    def test_user_without_a_crontab_is_ok(self, host: dict[str, Path]) -> None:
        _stub(host["bin"], "crontab", "echo 'no crontab for manta' >&2; exit 1\n")
        result = _check(host)
        assert "OK user-crontab" in result.stdout

    def test_clean_crontab_is_ok(self, host: dict[str, Path]) -> None:
        _stub(
            host["bin"],
            "crontab",
            "echo '@reboot rclone mount google-drive: ~/GoogleDrive'\n",
        )
        result = _check(host)
        assert "OK user-crontab" in result.stdout

    def test_missing_bucket_in_env_is_an_error(self, host: dict[str, Path]) -> None:
        host["env"].write_text("MT_BACKUP_S3_ENDPOINT=x\n")
        result = _check(host)
        assert result.returncode == 1
        assert "MT_BACKUP_S3_BUCKET" in result.stderr

    def test_interval_constant_defined_once(self) -> None:
        hits = subprocess.run(
            ["grep", "-rn", "WAL_OFFSITE_INTERVAL_MIN", "deploy", "scripts"],
            cwd=_REPO_ROOT, capture_output=True, text=True,
        ).stdout.splitlines()  # fmt: skip
        definitions = [h for h in hits if "WAL_OFFSITE_INTERVAL_MIN=" in h]
        assert len(definitions) == 1, definitions
        assert definitions[0].startswith("deploy/setup-backup.sh:")


# --- deploy/lib/pg_settings.sh -----------------------------------------------

_PG = _LIB / "pg_settings.sh"
_ARCHIVE_CMD = "test ! -f /d/wal/%f.zst && zstd -q -T2 %p -o /d/wal/%f.zst.tmp"


def _pg_rows(archive_command: str, source: str, pending: str = "f") -> str:
    return (
        f"archive_command|{archive_command}|{source}|{pending}\n"
        "archive_mode|on|/etc/postgresql/17/main/postgresql.auto.conf|f\n"
        "wal_compression|zstd|/etc/postgresql/17/main/postgresql.auto.conf|f\n"
    )


@pytest.fixture
def pg(tmp_path: Path) -> dict[str, Path]:
    """psql stub: answers pg_settings from a fixture, logs every other -c."""
    fixture = tmp_path / "rows"
    after = tmp_path / "rows.after"
    log = tmp_path / "statements"
    conf_dir = tmp_path / "pg"
    (conf_dir / "conf.d").mkdir(parents=True)
    (conf_dir / "postgresql.conf").write_text("# empty\n")
    body = f"""
rows={str(fixture)!r}
[ -e {str(log)!r}.altered ] && [ -e {str(after)!r} ] && rows={str(after)!r}
while [ $# -gt 0 ]; do
  case "$1" in
    -c) stmt="$2"; shift 2
        case "$stmt" in
          *pg_settings*) cat "$rows" ;;
          *) echo "$stmt" >> {str(log)!r}
             case "$stmt" in *"ALTER SYSTEM"*) touch {str(log)!r}.altered ;; esac ;;
        esac ;;
    *) shift ;;
  esac
done
"""
    _stub(tmp_path / "bin", "psql", body)
    return {
        "fixture": fixture,
        "after": after,
        "log": log,
        "conf": conf_dir / "postgresql.conf",
        "bin": tmp_path / "bin",
    }


def _pg_run(pg: dict[str, Path], *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_PG),
            *extra,
            "--conf",
            str(pg["conf"]),
            "--set",
            "archive_mode=on",
            "--set",
            f"archive_command={_ARCHIVE_CMD}",
            "--set",
            "wal_compression=zstd",
            "--forbid-conf-line",
            "archive_command",
        ],  # fmt: skip
        pg["bin"],
    )


def _statements(pg: dict[str, Path]) -> list[str]:
    return pg["log"].read_text().splitlines() if pg["log"].exists() else []


class TestPgSettings:
    def test_matching_settings_issue_no_statements(self, pg: dict[str, Path]) -> None:
        pg["fixture"].write_text(
            _pg_rows(_ARCHIVE_CMD, "/etc/postgresql/17/main/postgresql.auto.conf")
        )
        result = _pg_run(pg)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _statements(pg) == []
        assert "OK archive_command" in result.stdout
        assert "OK archive_command conf-line" in result.stdout

    def test_drift_applies_exactly_those_settings_and_one_reload(
        self, pg: dict[str, Path]
    ) -> None:
        pg["fixture"].write_text(
            _pg_rows(
                "cp %p /old/%f", "/etc/postgresql/17/main/conf.d/915-archiving.conf"
            )
        )
        pg["after"].write_text(
            _pg_rows(_ARCHIVE_CMD, "/etc/postgresql/17/main/postgresql.auto.conf")
        )
        result = _pg_run(pg)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _statements(pg) == [
            f"ALTER SYSTEM SET archive_command = '{_ARCHIVE_CMD}';",
            "SELECT pg_reload_conf();",
        ]
        assert "DRIFT archive_command" in result.stdout
        assert "APPLIED archive_command" in result.stdout
        assert "APPLIED archive_mode" not in result.stdout
        assert "restart" not in result.stdout.lower().replace("pending restart", "")

    def test_check_mode_reports_drift_without_statements(
        self, pg: dict[str, Path]
    ) -> None:
        pg["fixture"].write_text(
            _pg_rows(
                "cp %p /old/%f", "/etc/postgresql/17/main/conf.d/915-archiving.conf"
            )
        )
        result = _pg_run(pg, "--check")
        assert result.returncode == 1
        assert _statements(pg) == []
        assert "DRIFT archive_command" in result.stdout
        assert "DRIFT archive_command source postgresql.auto.conf" in result.stdout

    def test_source_not_auto_conf_is_drift(self, pg: dict[str, Path]) -> None:
        pg["fixture"].write_text(
            _pg_rows(_ARCHIVE_CMD, "/etc/postgresql/17/main/conf.d/915-archiving.conf")
        )
        result = _pg_run(pg, "--check")
        assert result.returncode == 1
        assert "OK archive_command\n" in result.stdout
        expected = (
            "DRIFT archive_command source postgresql.auto.conf "
            "'/etc/postgresql/17/main/conf.d/915-archiving.conf'"
        )
        assert expected in result.stdout

    def test_matching_value_from_other_file_is_persisted_by_alter_system(
        self, pg: dict[str, Path]
    ) -> None:
        # archive_mode = on, but served from conf.d: apply must ALTER SYSTEM it
        # so the value survives removal of that file (2026-09-07 finding).
        auto = "/etc/postgresql/17/main/postgresql.auto.conf"
        pg["fixture"].write_text(
            _pg_rows(_ARCHIVE_CMD, auto).replace(
                f"archive_mode|on|{auto}",
                "archive_mode|on|/etc/postgresql/17/main/conf.d/915-archiving.conf",
            )
        )
        pg["after"].write_text(_pg_rows(_ARCHIVE_CMD, auto))
        result = _pg_run(pg)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _statements(pg) == [
            "ALTER SYSTEM SET archive_mode = 'on';",
            "SELECT pg_reload_conf();",
        ]
        assert "APPLIED archive_mode" in result.stdout

    def test_pending_restart_is_reported_never_acted_on(
        self, pg: dict[str, Path]
    ) -> None:
        pg["fixture"].write_text(
            _pg_rows(
                _ARCHIVE_CMD,
                "/etc/postgresql/17/main/postgresql.auto.conf",
                pending="t",
            )
        )
        result = _pg_run(pg)
        assert result.returncode == 1
        assert "PENDING RESTART archive_command" in result.stdout
        assert _statements(pg) == []

    def test_disabled_archive_command_is_pending_restart_not_drift(
        self, pg: dict[str, Path]
    ) -> None:
        auto = "/etc/postgresql/17/main/postgresql.auto.conf"
        pg["fixture"].write_text(_pg_rows("(disabled)", auto, pending="f"))
        result = _pg_run(pg)
        assert result.returncode == 1
        assert "PENDING RESTART archive_command" in result.stdout
        assert "DRIFT archive_command" not in result.stdout
        assert _statements(pg) == []

    def test_hand_line_in_conf_d_is_drift(self, pg: dict[str, Path]) -> None:
        pg["fixture"].write_text(
            _pg_rows(_ARCHIVE_CMD, "/etc/postgresql/17/main/postgresql.auto.conf")
        )
        hand = pg["conf"].parent / "conf.d" / "915-archiving.conf"
        hand.write_text(
            "archive_mode = on\narchive_command = 'test ! -f x && cp %p x'\n"
        )
        result = _pg_run(pg, "--check")
        assert result.returncode == 1
        assert f"DRIFT archive_command conf-line {hand}:2" in result.stdout

    def test_commented_line_is_not_drift(self, pg: dict[str, Path]) -> None:
        pg["fixture"].write_text(
            _pg_rows(_ARCHIVE_CMD, "/etc/postgresql/17/main/postgresql.auto.conf")
        )
        pg["conf"].write_text("#archive_command = ''\n")
        assert _pg_run(pg, "--check").returncode == 0

    def test_as_user_wraps_every_psql_call_in_runuser(
        self, pg: dict[str, Path]
    ) -> None:
        # runuser stub: record the user, then run the wrapped command.
        _stub(
            pg["bin"],
            "runuser",
            f'echo "runuser $2" >> {str(pg["log"])!r}\nshift 3\nexec "$@"\n',
        )
        pg["fixture"].write_text(
            _pg_rows(
                "cp %p /old/%f", "/etc/postgresql/17/main/conf.d/915-archiving.conf"
            )
        )
        pg["after"].write_text(
            _pg_rows(_ARCHIVE_CMD, "/etc/postgresql/17/main/postgresql.auto.conf")
        )
        result = _pg_run(pg, "--as-user", "postgres")
        assert result.returncode == 0, result.stdout + result.stderr
        statements = _statements(pg)
        assert statements.count("runuser postgres") == 3  # query, apply, re-query
        assert f"ALTER SYSTEM SET archive_command = '{_ARCHIVE_CMD}';" in statements

    def test_psql_failure_is_missing(self, pg: dict[str, Path]) -> None:
        _stub(pg["bin"], "psql", "echo 'psql: error: connection refused' >&2; exit 2\n")
        result = _pg_run(pg, "--check")
        assert result.returncode == 1
        assert (
            "MISSING pg-settings psql failed: psql: error: connection refused"
            in result.stdout
        )


# --- deploy/lib/render_cron.sh -------------------------------------------------

_RENDER = _LIB / "render_cron.sh"
_CLUSTER_TEMPLATE = _DEPLOY / "cron.d" / "manta-trading-backup.cluster"
_TABLE = _DEPLOY / "backup-clusters.conf"
_PRE227 = Path(__file__).parent / "fixtures" / "cron_main_pre227.txt"
# Production's values, so the 17/main render compares with the installed file.
_PROD_CHECKOUT = "/home/manta/source/repos/manta/trading-data"
_PROD_BUCKET = "b2:manta.trading.data"
_PROD_PGDATA = {
    "17/main": "/var/lib/postgresql/17/main",
    "17/tick": "/data/postgresql/17/tick",
}
# The argument pairs 227 adds to production's lines (TD3).
_NEW_MAIN_ARGS = (
    " --url-key MT_TIMESCALE_MAINTENANCE_URL",
    " --replication-host 127.0.0.1",
    f" --remote {_PROD_BUCKET} ",
)


def _render(
    interval: str,
    template: Path = _TEMPLATE,
    out: Path | None = None,
    table: Path = _TABLE,
    pgdata: dict[str, str] = _PROD_PGDATA,
) -> subprocess.CompletedProcess[str]:
    args = [
        str(_RENDER),
        "--template", str(template),
        "--cluster-template", str(_CLUSTER_TEMPLATE),
        "--clusters", str(table),
        "--interval", interval,
        "--checkout", _PROD_CHECKOUT,
        "--env-file", f"{_PROD_CHECKOUT}/.env",
        "--host-root", "/data/backup",
        "--cron-user", "manta",
        "--keep-days", "7",
        "--remote-prefix", _PROD_BUCKET,
        "--restic-prefix", "system",
    ]  # fmt: skip
    for cluster, path in pgdata.items():
        args += ["--pgdata", f"{cluster}={path}"]
    if out is not None:
        args += ["--out", str(out)]
    return _run(args)


def _jobs(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if line and not line.startswith("#") and "=" not in line.split()[0]
    ]  # fmt: skip


def _block(text: str, cluster: str) -> list[str]:
    """The job lines after `# cluster <name>` up to the next comment."""
    lines = text.splitlines()
    start = lines.index(f"# cluster {cluster}") + 1
    block = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        block.append(line)
    return block


def _host_block(text: str) -> list[str]:
    return [j for j in _jobs(text) if " root " in j]


def _strip_new_args(line: str) -> str:
    for arg in _NEW_MAIN_ARGS:
        line = line.replace(arg, " " if arg.endswith(" ") else "")
    return line


class TestRenderCron:
    def test_hourly_interval(self, tmp_path: Path) -> None:
        out = tmp_path / "cron"
        result = _render("60", out=out)
        assert result.returncode == 0, result.stderr
        text = out.read_text()
        jobs = _jobs(text)
        assert len(jobs) == 10  # 4 per cluster, 2 for the host
        assert [j.split()[5] for j in jobs] == ["manta"] * 8 + ["root"] * 2
        push = [j for j in jobs if "sync_wal_offsite.sh" in j][0]
        assert push.startswith("0 * * * * manta ")
        assert "--timeout 59 " in push
        health = [j for j in jobs if "backup_health_cron.sh" in j][0]
        assert "--stale-after 180 " in health
        assert "@" not in text.replace("@192", "")
        assert "%" not in text
        assert text.endswith("\n") and not text.endswith("\n\n")

    def test_block_order(self) -> None:
        text = _render("60").stdout
        comments = [x for x in text.splitlines() if x.startswith("# cluster ")]
        assert comments == ["# cluster 17/main", "# cluster 17/tick"]
        assert text.index("# cluster 17/tick") < text.index("# host:")
        assert "##" not in text, "template comments must not be rendered"

    def test_production_lines_gain_only_the_new_arguments(self) -> None:
        """TD3: strip the three new argument pairs from the 17/main and host
        lines; the result is the installed pre-227 file, line for line."""
        text = _render("60").stdout
        rendered = _block(text, "17/main") + _host_block(text)
        assert [_strip_new_args(x) for x in rendered] == (
            _PRE227.read_text().splitlines()
        )

    def test_production_blocks_never_name_tick(self) -> None:
        text = _render("60").stdout
        for line in _block(text, "17/main") + _host_block(text):
            assert "17-tick" not in line

    def test_tick_block(self) -> None:
        tick = _block(_render("60").stdout, "17/tick")
        assert len(tick) == 4
        health, push, metadata, weekly = tick
        for line in tick:
            paths = [w for w in line.split() if w.startswith("/data/")]
            assert paths
            for path in paths:
                assert path.startswith("/data/backup/17-tick/") or path in (
                    "/data/backup/17-tick",
                    "/data/backup/system-backup.stamp",
                    "/data/postgresql/17/tick",
                ), path
            assert "--replication-host" not in line
        assert "--url-key MT_TICK_MAINTENANCE_URL " in health
        assert "--system-stamp /data/backup/system-backup.stamp " in health
        assert "--pgdata /data/postgresql/17/tick " in health
        assert f"--remote {_PROD_BUCKET}/17-tick/wal " in push
        assert metadata.startswith("15 2 * * * manta ")
        assert f"--remote {_PROD_BUCKET}/17-tick --dest" in metadata
        assert weekly.startswith("30 2 * * 0 manta ")
        assert "--url-key MT_TICK_MAINTENANCE_URL --backup-root" in weekly
        assert f"--remote-base {_PROD_BUCKET}/17-tick/base " in weekly

    def test_missing_pgdata_for_a_row_refused(self) -> None:
        result = _render("60", pgdata={"17/main": "/x"})
        assert result.returncode == 2
        assert "no --pgdata for cluster 17/tick" in result.stderr
        assert result.stdout == ""

    def test_malformed_table_refused(self, tmp_path: Path) -> None:
        table = tmp_path / "bad.conf"
        table.write_text("17/main MT_A /data/x - -\n")
        result = _render("60", table=table)
        assert result.returncode == 1
        assert "line 1:" in result.stderr
        assert result.stdout == ""

    def test_fifteen_minute_interval(self) -> None:
        result = _render("15")
        assert result.returncode == 0, result.stderr
        push = [j for j in _jobs(result.stdout) if "sync_wal_offsite.sh" in j][0]
        assert push.startswith("*/15 * * * * manta ")
        assert "--timeout 14 " in push
        health = [j for j in _jobs(result.stdout) if "backup_health_cron.sh" in j][0]
        assert "--stale-after 45 " in health

    @pytest.mark.parametrize("bad", ["45", "0", "61", "x"])
    def test_bad_interval_refused(self, bad: str) -> None:
        result = _render(bad)
        assert result.returncode == 2
        assert result.stdout == ""

    def test_rerender_is_identical(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        _render("60", out=a)
        _render("60", out=b)
        assert a.read_bytes() == b.read_bytes()

    def test_unfilled_placeholder_refused(self, tmp_path: Path) -> None:
        tpl = tmp_path / "tpl"
        tpl.write_text(_TEMPLATE.read_text() + "0 6 * * * @CRON_USER@ @BOGUS@/x\n")
        result = _render("60", template=tpl)
        assert result.returncode == 1
        assert "@BOGUS@" in result.stderr

    def test_unescaped_percent_refused(self, tmp_path: Path) -> None:
        tpl = tmp_path / "tpl"
        tpl.write_text(_TEMPLATE.read_text() + "0 6 * * * root date +%Y\n")
        result = _render("60", template=tpl)
        assert result.returncode == 1
        assert "%" in result.stderr

    def test_ampersand_survives_substitution(self) -> None:
        """bash 5.2+ patsub_replacement: `2>&1` must not become `2>@X@1`."""
        for job in _jobs(_render("60").stdout):
            assert job.endswith("2>&1"), job


# --- deploy/lib/timeshift_merge.sh ---------------------------------------------

_TS = _LIB / "timeshift_merge.sh"


def _ts(file: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    args = [str(_TS), *extra, "--file", str(file), "--count-weekly", "2"]
    for x in _EXCLUDES:
        args += ["--exclude", x]
    return _run(args)


def _unmanaged(path: Path) -> dict:
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if k not in _MANAGED_KEYS}


class TestTimeshiftMerge:
    def test_check_reports_count_weekly_drift_and_uuid(self, tmp_path: Path) -> None:
        f = tmp_path / "timeshift.json"
        f.write_text(_LIVE_TIMESHIFT)
        result = _ts(f, "--check")
        assert result.returncode == 1
        assert "DRIFT count_weekly 2 3" in result.stdout
        assert (
            "INFO timeshift-device-uuid 277accd4-f2ef-4814-af94-f127b06eaf4b"
            in result.stdout
        )
        assert "OK exclude" in result.stdout
        assert "OK schedule_weekly" in result.stdout
        assert f.read_text() == _LIVE_TIMESHIFT, "--check must not write"

    def test_apply_changes_only_managed_keys(self, tmp_path: Path) -> None:
        f = tmp_path / "timeshift.json"
        f.write_text(_LIVE_TIMESHIFT)
        before = _unmanaged(f)
        result = _ts(f)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "APPLIED count_weekly" in result.stdout
        after = json.loads(f.read_text())
        assert after["count_weekly"] == "2"
        assert after["schedule_weekly"] == "true"
        assert after["schedule_daily"] == "false"
        assert sorted(after["exclude"]) == sorted(_EXCLUDES)
        assert _unmanaged(f) == before
        assert after["backup_device_uuid"] == "277accd4-f2ef-4814-af94-f127b06eaf4b"
        assert after["snapshot_count"] == "421548"

    def test_conformant_file_is_untouched_byte_for_byte(self, tmp_path: Path) -> None:
        f = tmp_path / "timeshift.json"
        f.write_text(
            _LIVE_TIMESHIFT.replace('"count_weekly" : "3"', '"count_weekly" : "2"')
        )
        raw = f.read_bytes()
        result = _ts(f)
        assert result.returncode == 0, result.stdout
        assert "APPLIED" not in result.stdout
        assert f.read_bytes() == raw

    def test_second_apply_is_a_noop(self, tmp_path: Path) -> None:
        f = tmp_path / "timeshift.json"
        f.write_text(_LIVE_TIMESHIFT)
        _ts(f)
        raw = f.read_bytes()
        result = _ts(f)
        assert "APPLIED" not in result.stdout
        assert f.read_bytes() == raw

    def test_missing_file_is_reported_and_not_created(self, tmp_path: Path) -> None:
        f = tmp_path / "timeshift.json"
        result = _ts(f)
        assert result.returncode == 1
        assert f"MISSING timeshift-config {f}" in result.stdout
        assert not f.exists()


# --- deploy/lib/restic_repo.sh -------------------------------------------------

_RESTIC = _LIB / "restic_repo.sh"


@pytest.fixture
def restic(tmp_path: Path) -> dict[str, Path]:
    """restic stub: `cat config` succeeds iff a marker exists; logs argv+env."""
    marker = tmp_path / "repo-exists"
    log = tmp_path / "restic.log"
    body = f"""
echo "argv: $*" >> {str(log)!r}
echo "repo: $RESTIC_REPOSITORY key: $AWS_ACCESS_KEY_ID pw: $RESTIC_PASSWORD" \\
  >> {str(log)!r}
case "$1" in
  cat)  [ -e {str(marker)!r} ] ;;
  init) touch {str(marker)!r} ;;
  *)    exit 0 ;;
esac
"""
    _stub(tmp_path / "bin", "restic", body)
    return {
        "marker": marker,
        "log": log,
        "env": _env_file(tmp_path / "env"),
        "bin": tmp_path / "bin",
    }


def _restic(r: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [str(_RESTIC), "--env-file", str(r["env"]), "--prefix", "system", *args],
        r["bin"],
    )


class TestResticRepo:
    def test_check_missing_repo(self, restic: dict[str, Path]) -> None:
        result = _restic(restic, "check")
        assert result.returncode == 1
        expected = (
            "MISSING restic-repo s3:https://s3.example.invalid/bucket-x/system "
            "(not initialised)"
        )
        assert expected in result.stdout
        assert "argv: init" not in restic["log"].read_text()

    def test_init_creates_repo_then_ok(self, restic: dict[str, Path]) -> None:
        result = _restic(restic, "init")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "APPLIED restic-repo" in result.stdout
        assert "OK restic-repo" in result.stdout
        log = restic["log"].read_text()
        assert "argv: init" in log
        assert (
            "repo: s3:https://s3.example.invalid/bucket-x/system key: keyid pw: pw"
            in log
        )

    def test_existing_repo_is_ok_without_init(self, restic: dict[str, Path]) -> None:
        restic["marker"].touch()
        result = _restic(restic, "init")
        assert result.returncode == 0
        assert "APPLIED" not in result.stdout
        assert "argv: init" not in restic["log"].read_text()

    def test_missing_password_blocks_without_calling_restic(
        self, restic: dict[str, Path]
    ) -> None:
        _env_file(restic["env"], password=False)
        result = _restic(restic, "check")
        assert result.returncode == 1
        assert "MISSING restic-password MT_BACKUP_RESTIC_PASSWORD" in result.stdout
        assert not restic["log"].exists()

    def test_run_passes_arguments_and_environment(
        self, restic: dict[str, Path]
    ) -> None:
        result = _restic(restic, "run", "--", "backup", "/etc", "--tag", "x")
        assert result.returncode == 0
        assert "argv: backup /etc --tag x" in restic["log"].read_text()

    def test_requires_a_command(self, restic: dict[str, Path]) -> None:
        assert _restic(restic).returncode == 2


# --- Runbook / script consistency (Task 4.4) ----------------------------------

_RUNBOOK = (
    _REPO_ROOT / "project-documents" / "user" / "runbooks" / "200-backup-and-restore.md"
)


def _archive_command_from_script(wal_dir: str) -> str:
    """The script's constant, rendered the way step 4 renders it."""
    text = _SETUP.read_text(encoding="utf-8")
    matches = [
        line
        for line in text.splitlines()
        if line.startswith("ARCHIVE_COMMAND_TEMPLATE=")
    ]
    assert len(matches) == 1, matches
    template = matches[0].split("=", 1)[1].strip("'")
    return template.replace("@WAL_DIR@", wal_dir)


class TestRunbookConsistency:
    def test_runbook_carries_the_scripts_archive_command_verbatim(self) -> None:
        command = _archive_command_from_script("/data/backup/wal")
        assert "@WAL_DIR@" not in command
        assert f"archive_command = '{command}'" in _RUNBOOK.read_text(encoding="utf-8")

    def test_runbook_restore_command_handles_both_shapes(self) -> None:
        text = _RUNBOOK.read_text(encoding="utf-8")
        assert text.count("zstd -dq") >= 1
        assert "|| cp /data/backup/wal/%f %p" in text
