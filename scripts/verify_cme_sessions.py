"""Run every record of local Databento jobs through a calendar's sessions (221).

Usage::

    uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY \\
        /data/market-data/databento/GLBX-20240930-USM7UXXJBA <unzipped job dir> ...

Each job directory must hold the job's ``manifest.json`` and every DBN file it
lists (unzip a delivered ``.zip`` first). Every file's size and SHA-256 are
checked against the manifest, then its records are decoded with the 220 reader
and each ``ts_event`` is assigned through ``SessionIndex.locate_ns``. Sessions
come from ``TradingCalendar.sessions_between`` against ``MT_TIMESCALE_DB_URL``.

Exit codes:
    0  every record lies inside a session
    1  records outside any session (each is printed with its nearest sessions)
    2  a missing job directory, manifest or listed file; a size or checksum
       mismatch; a decode failure; or the files' span outside the populated
       sessions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from manta_trading.config import Settings
from manta_trading.data.base.session_index import NO_SESSION, Session, SessionIndex
from manta_trading.data.base.trading_calendar import (
    OutOfPopulatedRangeError,
    TradingCalendar,
)
from manta_trading.data.tick.databento.dbn_file import DbnFile, DbnFileReader

EXIT_CLEAN = 0
EXIT_OUTSIDE_SESSION = 1
EXIT_INPUT_ERROR = 2

DBN_SUFFIX = ".dbn.zst"
MAX_PRINTED_OUTSIDE = 100
"""Outside-session records printed in full; the count is always exact."""

_INT64_MAX = np.iinfo(np.int64).max
_INT64_MIN = np.iinfo(np.int64).min
_HASH_CHUNK_BYTES = 1 << 20


class InputError(Exception):
    """A job, manifest or file problem: exit 2, naming the path."""


@dataclass
class Tally:
    """Records seen so far, per job and per session position."""

    sessions: int
    per_job: dict[str, int] = field(default_factory=dict)
    outside: list[int] = field(default_factory=list)
    outside_count: int = 0

    def __post_init__(self) -> None:
        self.first = np.full(self.sessions, _INT64_MAX, dtype=np.int64)
        self.last = np.full(self.sessions, _INT64_MIN, dtype=np.int64)
        self.count = np.zeros(self.sessions, dtype=np.int64)


def _listed_files(job_dir: Path) -> list[tuple[Path, int, str]]:
    """``(path, size, sha256)`` for every DBN file the job's manifest lists."""
    if not job_dir.is_dir():
        raise InputError(f"{job_dir}: job directory not found")
    manifest_path = job_dir / "manifest.json"
    if not manifest_path.is_file():
        raise InputError(f"{manifest_path}: manifest.json not found")
    manifest = json.loads(manifest_path.read_text())
    listed = []
    for entry in manifest["files"]:
        if not entry["filename"].endswith(DBN_SUFFIX):
            continue
        algorithm, _, digest = entry["hash"].partition(":")
        if algorithm != "sha256":
            raise InputError(f"{manifest_path}: unsupported hash {entry['hash']}")
        listed.append((job_dir / entry["filename"], int(entry["size"]), digest))
    if not listed:
        raise InputError(f"{manifest_path}: lists no {DBN_SUFFIX} files")
    return sorted(listed)


def _verify_file(path: Path, size: int, sha256: str) -> None:
    if not path.is_file():
        raise InputError(f"{path}: listed in manifest.json but missing")
    if path.stat().st_size != size:
        raise InputError(f"{path}: size {path.stat().st_size} != manifest {size}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise InputError(f"{path}: SHA-256 does not match manifest.json")


def _file_span(paths: list[Path]) -> tuple[datetime, datetime]:
    """Earliest header start and latest header end across ``paths``."""
    reader = DbnFileReader()
    starts, ends = [], []
    for path in paths:
        opened = _open(reader, path)
        starts.append(opened.start)
        ends.append(opened.end)
    return min(starts), max(ends)


def _open(reader: DbnFileReader, path: Path) -> DbnFile:
    try:
        return reader.open_file(path)
    except (ValueError, OSError) as exc:
        raise InputError(f"{path}: cannot decode: {exc}") from exc


def _assign(path: Path, index: SessionIndex, tally: Tally) -> int:
    """Assign every record of ``path``; return its record count."""
    records = 0
    try:
        for batch in _open(DbnFileReader(), path).iter_batches():
            ts = batch.records["ts_event"].astype(np.int64)
            pos = index.locate_ns(ts)
            inside = pos != NO_SESSION
            np.minimum.at(tally.first, pos[inside], ts[inside])
            np.maximum.at(tally.last, pos[inside], ts[inside])
            np.add.at(tally.count, pos[inside], 1)
            outside = ts[~inside]
            tally.outside_count += len(outside)
            room = MAX_PRINTED_OUTSIDE - len(tally.outside)
            tally.outside.extend(outside[:room].tolist())
            records += batch.count
    except (ValueError, OSError) as exc:
        raise InputError(f"{path}: decode failed: {exc}") from exc
    return records


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _utc(ns: int) -> datetime:
    return _EPOCH + timedelta(microseconds=ns // 1_000)


def _ns(ts: datetime) -> int:
    """Exact epoch nanoseconds (datetime resolution is microseconds)."""
    return (ts - _EPOCH) // timedelta(microseconds=1) * 1_000


def _nearest(ns: int, sessions: tuple[Session, ...]) -> str:
    """The sessions either side of an outside-session instant."""
    opens = np.array([_ns(s.open_utc) for s in sessions], dtype=np.int64)
    after = int(np.searchsorted(opens, ns, side="right"))
    before_text = (
        f"after {sessions[after - 1].session_date} (closed "
        f"{sessions[after - 1].close_utc.isoformat()})"
        if after > 0
        else "before the first session"
    )
    after_text = (
        f"before {sessions[after].session_date} (opens "
        f"{sessions[after].open_utc.isoformat()})"
        if after < len(sessions)
        else "after the last session"
    )
    return f"{before_text}, {after_text}"


def _report(
    tally: Tally,
    sessions: tuple[Session, ...],
    tz: ZoneInfo,
    exceptions: dict[date, str],
) -> None:
    for job, records in tally.per_job.items():
        print(f"{job}: {records:,} records (files verified against manifest.json)")
    print(f"outside any session: {tally.outside_count}")
    for ns in tally.outside:
        print(f"  {_utc(ns).isoformat()} — {_nearest(ns, sessions)}")
    if tally.outside_count > len(tally.outside):
        print(f"  ... {tally.outside_count - len(tally.outside)} more not printed")
    print(
        f"\n{'session':<11} {'open':<17} {'first trade':<22} "
        f"{'last trade':<22} {'close':<17} {'records':>11}  exception"
    )
    fmt = "%m-%d %H:%M:%S.%f"
    for i, session in enumerate(sessions):
        if tally.count[i] == 0:
            continue
        print(
            f"{session.session_date!s:<11} "
            f"{session.open_utc.astimezone(tz):%m-%d %H:%M %Z}  "
            f"{_utc(int(tally.first[i])).astimezone(tz).strftime(fmt):<22} "
            f"{_utc(int(tally.last[i])).astimezone(tz).strftime(fmt):<22} "
            f"{session.close_utc.astimezone(tz):%m-%d %H:%M %Z}  "
            f"{int(tally.count[i]):>11,}  "
            f"{exceptions.get(session.session_date, '')}"
        )


def run(calendar_id: str, job_dirs: list[Path]) -> int:
    jobs = {job: _listed_files(job) for job in job_dirs}
    for listed in jobs.values():
        for path, size, sha256 in listed:
            _verify_file(path, size, sha256)
    start, end = _file_span([p for listed in jobs.values() for p, _, _ in listed])

    cal = TradingCalendar(calendar_id, str(Settings().timescale_db_url))
    try:
        sessions = tuple(cal.sessions_between(start, end))
        # Loads the calendar's metadata too, which sets cal.timezone.
        exceptions = {
            h.holiday_date: h.holiday_name
            for year in range(start.year, end.year + 1)
            for h in cal.get_holidays(year)
        }
        tz = cal.timezone
    except OutOfPopulatedRangeError as exc:
        raise InputError(f"files span {start}..{end}: {exc}") from exc
    finally:
        cal.close()
    if tz is None:
        raise InputError(f"calendar {calendar_id}: no time zone loaded")

    index = SessionIndex(sessions)
    tally = Tally(sessions=len(sessions))
    for job, listed in jobs.items():
        tally.per_job[job.name] = sum(_assign(p, index, tally) for p, _, _ in listed)
    _report(tally, sessions, tz, exceptions)
    return EXIT_OUTSIDE_SESSION if tally.outside_count else EXIT_CLEAN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--calendar", required=True, help="Calendar ID, e.g. CME_EQUITY"
    )
    parser.add_argument("job_dirs", nargs="+", type=Path, help="Unzipped job dirs")
    args = parser.parse_args()
    try:
        return run(args.calendar, args.job_dirs)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR


if __name__ == "__main__":
    sys.exit(main())
