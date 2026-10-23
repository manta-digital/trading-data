"""The tick provisioning script's ``.env`` writer and log scrub (226 TD1).

Both are bash functions in ``deploy/lib``; each test sources the library and
calls the function, as ``scripts/provision_tick_cluster.sh`` does. No real
credential appears here: every password is a generated stand-in.
"""

from __future__ import annotations

import getpass
import grp
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path

import pytest

from manta_trading.data.tick.constants import TICK_ENV_PREFIX
from manta_trading.data.tick.store_context import check_env_keys

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "deploy" / "lib"
SCRIPT = ROOT / "scripts" / "provision_tick_cluster.sh"
ME = getpass.getuser()


def _bash(lib: str, *args: str) -> subprocess.CompletedProcess[str]:
    """``. <lib>; <args>`` with each argument passed positionally, unquoted."""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB / lib}"; "$@"', "bash", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _add(env: Path, owner: str, *pairs: str) -> subprocess.CompletedProcess[str]:
    return _bash("env_add_keys.sh", "env_add_keys", str(env), owner, *pairs)


def _script_url_keys() -> list[str]:
    """The keys the provisioning script writes, read from its ``URL_KEYS``."""
    keys = re.findall(r"^\s*\[(MT_[A-Z0-9_]+)\]=", SCRIPT.read_text(), re.MULTILINE)
    assert sorted(keys) == ["MT_TICK_DB_URL", "MT_TICK_MAINTENANCE_URL"], keys
    return keys


# -- env_add_keys -------------------------------------------------------------------


def test_adds_only_absent_keys_and_keeps_existing_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEEP=old\nMT_TICK_DB_URL=already-there\n")
    result = _add(env, ME, "MT_TICK_DB_URL=new", "NEW_KEY=value")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    assert env.read_text() == "KEEP=old\nMT_TICK_DB_URL=already-there\nNEW_KEY=value\n"


def test_second_run_adds_nothing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _add(env, ME, "A=1", "B=2")
    again = _add(env, ME, "A=9", "B=9")
    assert again.stdout.strip() == "0"
    assert env.read_text() == "A=1\nB=2\n"


def test_mode_ends_0600_even_when_nothing_is_added(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    env.chmod(0o644)
    assert _add(env, ME, "A=2").returncode == 0
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_a_missing_final_newline_does_not_glue_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1")
    _add(env, ME, "B=2")
    assert env.read_text() == "A=1\nB=2\n"


def test_a_failure_part_way_leaves_the_old_file_whole(tmp_path: Path) -> None:
    """chown to an owner that does not exist fails after the temp file is
    written: the old file is untouched and no temp file is left behind."""
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    result = _add(env, "no-such-user-226", "B=2")
    assert result.returncode != 0
    assert env.read_text() == "A=1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_the_written_env_passes_the_tick_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every ``MT_TICK_*`` key the script writes is one a setting reads; the
    proof URLs are outside the prefix (TD1)."""
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX):
            monkeypatch.delenv(name)
    env = tmp_path / ".env"
    pairs = [f"{key}=postgresql://u:p@h:5433/db" for key in _script_url_keys()]
    assert _add(env, ME, *pairs).returncode == 0
    check_env_keys(env)


# -- env_set_key (slice 924 D4) -----------------------------------------------------


def _set(env: Path, owner: str, pair: str) -> subprocess.CompletedProcess[str]:
    return _bash("env_add_keys.sh", "env_set_key", str(env), owner, pair)


def test_set_key_replaces_a_changed_value_in_place(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nK=old\nB=2\n")
    result = _set(env, ME, "K=new")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "set\n"
    assert env.read_text() == "A=1\nK=new\nB=2\n"


def test_set_key_appends_when_absent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    assert _set(env, ME, "K=v").stdout == "set\n"
    assert env.read_text() == "A=1\nK=v\n"


def test_set_key_identical_value_is_unchanged_and_byte_identical(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    body = b"A=1\nK=v\n# note\n"
    env.write_bytes(body)
    assert _set(env, ME, "K=v").stdout == "unchanged\n"
    assert env.read_bytes() == body


def test_set_key_keeps_every_other_line_byte_identical(tmp_path: Path) -> None:
    password = secrets.token_urlsafe(16)
    env = tmp_path / ".env"
    lines = [
        f'MT_TIMESCALE_DB_URL="postgresql://app:{password}@h/db"',
        "",
        "# comment with K=old in it",
        "  INDENTED=1",
        "K=old",
        "Z=last",
    ]
    env.write_text("\n".join(lines) + "\n")
    _set(env, ME, "K=new")
    after = env.read_text().splitlines()
    assert after == [*lines[:4], "K=new", "Z=last"]


def test_set_key_original_without_trailing_newline(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nK=old")
    _set(env, ME, "K=new")
    assert env.read_text() == "A=1\nK=new\n"
    env.write_text("A=1")
    _set(env, ME, "K=v")
    assert env.read_text() == "A=1\nK=v\n"


def test_set_key_value_with_special_characters(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("K=old\n")
    value = r"a&b\c/d|e$f*g"
    _set(env, ME, f"K={value}")
    assert env.read_text() == f"K={value}\n"


def test_set_key_unwritable_directory_leaves_the_file_whole(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("K=old\n")
    tmp_path.chmod(0o555)
    try:
        result = _set(env, ME, "K=new")
    finally:
        tmp_path.chmod(0o755)
    assert result.returncode != 0
    assert env.read_text() == "K=old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_set_key_failed_chown_leaves_no_temp_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("K=old\n")
    assert _set(env, "no-such-user-924", "K=new").returncode != 0
    assert env.read_text() == "K=old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_set_key_never_prints_the_value(tmp_path: Path) -> None:
    secret = secrets.token_urlsafe(16)
    env = tmp_path / ".env"
    for owner in (ME, "no-such-user-924"):
        result = _set(env, owner, f"K={secret}")
        assert secret not in result.stdout + result.stderr


# -- backup_roots_env_set (slice 924 D4, success criterion 8) -----------------------

_CLUSTERS = ROOT / "deploy" / "backup-clusters.conf"
_GROUP = grp.getgrgid(os.getgid()).gr_name


def _roots(env: Path, conf: Path = _CLUSTERS) -> subprocess.CompletedProcess[str]:
    return _bash(
        "env_add_keys.sh", "backup_roots_env_set", str(env), ME, _GROUP, str(conf)
    )


def _mode_and_owner(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid


def test_backup_roots_from_the_real_table(tmp_path: Path) -> None:
    env = tmp_path / "manta-trading.env"
    others = ["MT_TIMESCALE_DB_URL=postgresql://x", "MT_LOG_LEVEL=INFO"]
    env.write_text("\n".join(others) + "\n")
    first = _roots(env)
    assert first.returncode == 0, first.stderr
    assert first.stdout == "set\n"
    assert env.read_text().splitlines() == [
        *others,
        "MT_BACKUP_ROOTS=/data/backup,/data/backup/17-tick",
    ]
    assert _mode_and_owner(env) == (0o640, os.getuid(), os.getgid())
    again = _roots(env)
    assert again.stdout == "unchanged\n"
    assert _mode_and_owner(env) == (0o640, os.getuid(), os.getgid())


def test_backup_roots_malformed_table_leaves_the_env_untouched(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "clusters.conf"
    conf.write_text(_CLUSTERS.read_text() + "17/bad MT_X relative/root\n")
    env = tmp_path / "manta-trading.env"
    body = b"A=1\n"
    env.write_bytes(body)
    env.chmod(0o600)
    result = _roots(env, conf)
    assert result.returncode == 2  # table error, distinct from a write failure
    assert "backup-clusters:" in result.stderr
    assert env.read_bytes() == body
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_backup_roots_value_parses_through_the_python_reader(tmp_path: Path) -> None:
    from manta_trading.cli.commands.health_backup import parse_backup_roots

    env = tmp_path / "manta-trading.env"
    _roots(env)
    (line,) = env.read_text().splitlines()
    value = line.removeprefix("MT_BACKUP_ROOTS=")
    assert parse_backup_roots(value) == ["/data/backup", "/data/backup/17-tick"]


# -- log_scrub ----------------------------------------------------------------------


def _scrub(log: Path, *secrets_: str) -> subprocess.CompletedProcess[str]:
    return _bash("log_scrub.sh", "log_scrub", str(log), *secrets_)


def test_a_planted_password_fails_the_scan_without_printing_it(tmp_path: Path) -> None:
    password = secrets.token_hex(24)
    log = tmp_path / "provision.log"
    log.write_text(f"==> role passwords\nAPPLIED {password}\nOK done\n")
    result = _scrub(log, password)
    assert result.returncode == 1
    assert result.stdout.strip() == f"LEAK {log}:2"
    assert password not in result.stdout + result.stderr


def test_a_url_with_a_password_fails_the_scan(tmp_path: Path) -> None:
    log = tmp_path / "provision.log"
    log.write_text(
        "ok\nMT_TICK_DB_URL=postgresql://tick_app:hunter2@manta9000:5433/x\n"
    )
    assert _scrub(log).returncode == 1


def test_a_clean_log_passes(tmp_path: Path) -> None:
    log = tmp_path / "provision.log"
    log.write_text(
        "OK MT_TICK_DB_URL present\n"
        "WOULD pg_createcluster 17 tick --port 5433\n"
        "postgresql://manta9000:5433/trading_tick has no password\n"
    )
    result = _scrub(log, secrets.token_hex(24), "")
    assert result.returncode == 0, result.stdout


# -- the shared .env URL pattern ------------------------------------------------


def _script_url_re() -> str:
    """The script's one ``PG_URL_RE`` assignment, as bash would assign it."""
    found = re.findall(r"^PG_URL_RE=(.+)$", SCRIPT.read_text(), re.MULTILINE)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_both_url_schemes_yield_the_password(scheme: str) -> None:
    """The password lookup and the production read share one pattern, so a
    ``postgres://`` URL never reads as password-less (226 review F005)."""
    pw = secrets.token_hex(8)
    url = f"{scheme}://tick_app:{pw}@manta9000:5433/trading_tick"
    done = subprocess.run(
        [
            "bash",
            "-c",
            f'PG_URL_RE={_script_url_re()}; [[ "$1" =~ $PG_URL_RE ]] '
            '&& echo "${BASH_REMATCH[3]}"',
            "bash",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.strip() == pw


# -- slice 227 TD5: replication, pg_hba, parent directory modes -----------------


def _script_function(name: str) -> str:
    """One function's source from the provisioning script, by its name."""
    text = SCRIPT.read_text()
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(0)


def _script_assignments(*names: str) -> str:
    text = SCRIPT.read_text()
    lines = []
    for name in names:
        match = re.search(rf"^{name}=.*$", text, re.MULTILINE)
        assert match, name
        lines.append(match.group(0))
    return "\n".join(lines)


def _rendered_hba() -> list[str]:
    """hba_content() as the script defines it, with the source address stubbed."""
    program = "\n".join(
        [
            _script_assignments(
                "PG_OWNER", "APP_ROLE", "MIGRATE_ROLE", "HBA_DATABASES"
            ),
            "client_source() { echo 127.0.0.1; }",
            _script_function("hba_content"),
            "hba_content",
        ]
    )
    result = subprocess.run(
        ["bash", "-c", program], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_hba_admits_replication_for_the_migrate_role() -> None:
    assert "host replication tick_migrate 127.0.0.1/32 scram-sha-256" in _rendered_hba()


def test_hba_admits_the_drill_database_and_not_the_proof_one() -> None:
    hosts = [
        x for x in _rendered_hba() if x.startswith("host ") and "replication" not in x
    ]
    assert hosts == [
        "host trading_tick,trading_tick_drill tick_app,tick_migrate"
        " 127.0.0.1/32 scram-sha-256"
    ]


def test_only_trading_tick_is_created() -> None:
    assert (
        _script_assignments("CREATED_DATABASES") == "CREATED_DATABASES=(trading_tick)"
    )


def test_parent_directories_755_data_directory_700() -> None:
    assert _script_assignments("PG_PARENT_DIR_MODE", "PG_DATA_DIR_MODE").split() == [
        "PG_PARENT_DIR_MODE=755",
        "PG_DATA_DIR_MODE=700",
    ]
    assert (
        _script_assignments("PG_PARENT_DIRS")
        == 'PG_PARENT_DIRS=("$DATA_ROOT" "$DATA_ROOT/$PG_VERSION")'
    )


def test_roles_are_applied_with_replication() -> None:
    body = _script_function("ensure_databases")
    assert '-v with_replication=1 -f - < "$ROLES_SQL"' in body


def test_roles_sql_grants_replication_only_when_asked() -> None:
    sql = (ROOT / "scripts" / "provision_tick_roles.sql").read_text()
    block = sql[sql.index("\\if :{?with_replication}\nSELECT") :]
    assert block.index("ALTER ROLE %I REPLICATION") < block.index("\\else")
