"""The tick provisioning script's ``.env`` writer and log scrub (226 TD1).

Both are bash functions in ``deploy/lib``; each test sources the library and
calls the function, as ``scripts/provision_tick_cluster.sh`` does. No real
credential appears here: every password is a generated stand-in.
"""

from __future__ import annotations

import getpass
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
    assert len(keys) == 4, keys
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
