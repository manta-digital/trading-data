#!/usr/bin/env python3
"""wal_segment_name.py — WAL segment-name arithmetic in one place (slice 920, D5).

A segment name is 24 hex characters: 8 for the timeline, 8 for the log-file
number, 8 for the segment within that log file. With 16 MiB segments a log
file holds 256 segments, so ``…FF`` rolls over to the next log file.

Usage:
    wal_segment_name.py next <segment-name>       the segment after the given one
    wal_segment_name.py from-lsn <tli> <lsn>      the segment holding an LSN
                                                  (LSN as PostgreSQL prints it,
                                                  e.g. ``1217/83A00000``)

Malformed input exits 2. Both the health check (next-to-archive name) and
the prune (oldest needed segment from a backup manifest) call this so the
arithmetic exists exactly once. Stdlib only.
"""

from __future__ import annotations

import sys

WAL_SEGMENT_BYTES = 16 * 1024 * 1024
# One log file spans 4 GiB of WAL (the low 32 bits of an LSN).
LOG_FILE_BYTES = 1 << 32
SEGMENTS_PER_LOG = LOG_FILE_BYTES // WAL_SEGMENT_BYTES
SEGMENT_NAME_LEN = 24
EXIT_USAGE = 2


class MalformedInput(ValueError):
    """Input is not a segment name / LSN / timeline in the expected shape."""


def _hex(text: str, what: str) -> int:
    try:
        return int(text, 16)
    except ValueError:
        raise MalformedInput(f"{what} is not hexadecimal: {text!r}") from None


def format_segment(tli: int, log: int, seg: int) -> str:
    return f"{tli:08X}{log:08X}{seg:08X}"


def parse_segment(name: str) -> tuple[int, int, int]:
    if len(name) != SEGMENT_NAME_LEN:
        raise MalformedInput(
            f"segment name must be {SEGMENT_NAME_LEN} hex characters: {name!r}"
        )
    return (
        _hex(name[0:8], "timeline"),
        _hex(name[8:16], "log number"),
        _hex(name[16:24], "segment number"),
    )


def next_segment(name: str) -> str:
    tli, log, seg = parse_segment(name)
    if seg >= SEGMENTS_PER_LOG:
        raise MalformedInput(
            f"segment number {seg:X} exceeds {SEGMENTS_PER_LOG - 1:X}: {name!r}"
        )
    seg += 1
    if seg == SEGMENTS_PER_LOG:
        log, seg = log + 1, 0
    return format_segment(tli, log, seg)


def segment_from_lsn(tli_text: str, lsn: str) -> str:
    if "/" not in lsn:
        raise MalformedInput(f"LSN must look like HIGH/LOW: {lsn!r}")
    high_text, low_text = lsn.split("/", 1)
    high = _hex(high_text, "LSN high part")
    low = _hex(low_text, "LSN low part")
    if not (0 <= low < LOG_FILE_BYTES):
        raise MalformedInput(f"LSN low part out of range: {lsn!r}")
    tli = int(tli_text) if tli_text.isdigit() else _hex(tli_text, "timeline")
    return format_segment(tli, high, low // WAL_SEGMENT_BYTES)


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "next":
            print(next_segment(argv[1]))
            return 0
        if len(argv) == 3 and argv[0] == "from-lsn":
            print(segment_from_lsn(argv[1], argv[2]))
            return 0
        print(__doc__.strip(), file=sys.stderr)
        return EXIT_USAGE
    except MalformedInput as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
