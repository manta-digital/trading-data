"""Derive ``MemoryMax`` for ``manta-acquisition.slice`` from the journal.

Slice 233 design, Technical Decision 2. Read-only: it reads unit files and
each member's journal, prints the derivation, and writes no file. The result
is copied by hand into ``deploy/systemd/manta-acquisition.slice``, with the
inputs and date in that file's comment.

    uv run python scripts/acquisition_memory_bound.py

Exit 0 on success; 2 when a member's journal cannot be read or holds no
``memory peak`` line in the window (no zero or default stands in for it).
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

#: Multiplier over the summed peaks; covers growth (233 design, TD 2).
MEMORY_BOUND_HEADROOM = 2

#: Journal window, in days, for each member's peak (233 design, TD 2).
JOURNAL_WINDOW_DAYS = 30

#: The slice whose members are discovered and bounded.
ACQUISITION_SLICE = "manta-acquisition.slice"

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "deploy" / "systemd"

GIB = 1024**3

# systemd formats sizes base-1024 (format_bytes); a bare number is bytes.
_SUFFIX_BYTES = {"": 1, "B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}

# The clause alone: systemd versions differ in what precedes and follows it.
_PEAK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s+memory peak", re.IGNORECASE)

_SLICE_RE = re.compile(
    rf"^\s*Slice\s*=\s*{re.escape(ACQUISITION_SLICE)}\s*$", re.MULTILINE
)


class MemoryBoundError(Exception):
    """The derivation cannot be made; the message names the member."""


def members(systemd_dir: Path = SYSTEMD_DIR) -> list[str]:
    """Unit names of the services that declare ``Slice=`` this slice."""
    return sorted(
        path.name
        for path in systemd_dir.glob("*.service")
        if _SLICE_RE.search(path.read_text(encoding="utf-8"))
    )


def parse_peak(line: str) -> int | None:
    """Bytes of the ``<size> memory peak`` clause of a ``Consumed`` line."""
    if "Consumed" not in line:
        return None
    match = _PEAK_RE.search(line)
    if match is None:
        return None
    value, suffix = match.groups()
    return int(float(value) * _SUFFIX_BYTES[suffix.upper()])


def max_peak(lines: Iterable[str], unit: str) -> int:
    """Largest peak across ``lines``; raises when none parse."""
    peaks = [p for p in map(parse_peak, lines) if p is not None]
    if not peaks:
        raise MemoryBoundError(
            f"{unit}: no 'memory peak' line in the last {JOURNAL_WINDOW_DAYS} days"
        )
    return max(peaks)


def memory_max_gib(peaks: Mapping[str, int]) -> int:
    """``ceil_GiB(MEMORY_BOUND_HEADROOM × Σ peaks)``; raises on no members."""
    if not peaks:
        raise MemoryBoundError(f"no unit declares Slice={ACQUISITION_SLICE}")
    return math.ceil(MEMORY_BOUND_HEADROOM * sum(peaks.values()) / GIB)


def read_journal(unit: str) -> list[str]:
    """The unit's journal for the window, one message per line."""
    cmd = [
        "journalctl",
        "-u",
        unit,
        "-o",
        "cat",
        "--no-pager",
        "--since",
        f"-{JOURNAL_WINDOW_DAYS}d",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MemoryBoundError(
            f"{unit}: journalctl exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.splitlines()


def derive(
    units: list[str], read: Callable[[str], list[str]]
) -> tuple[dict[str, int], int]:
    """Each member's peak and the resulting bound in GiB."""
    peaks = {unit: max_peak(read(unit), unit) for unit in units}
    return peaks, memory_max_gib(peaks)


def main() -> int:
    try:
        peaks, result = derive(members(), read_journal)
    except MemoryBoundError as exc:
        # Process boundary: only this script's own errors become exit 2;
        # anything else is a bug and keeps its traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for unit, peak in peaks.items():
        print(f"{unit:<28} {peak / 1024**2:>9.1f} MiB")
    total = sum(peaks.values()) / GIB
    print(
        f"sum {total:.2f} GiB x {MEMORY_BOUND_HEADROOM} -> MemoryMax={result}G "
        f"({JOURNAL_WINDOW_DAYS}-day window)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
