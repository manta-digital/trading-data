"""A job's data files by the UTC day their header starts on (224 TD8, 226 TD8).

Shared by delivery (``in_flight_files``) and adoption (``adopt``): a file is
matched to its unit by its header, never by its name. A file whose header the
reader refuses (``TickFileDecodeError``) cannot name its day, so it is
collected rather than raised: the caller fails every day no readable file
claims, naming the refused files, instead of calling those days provider
holes (one of them may be that day's file). One bad file fails units, not
the pass.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from manta_trading.data.tick.adopt_files import DATA_FILE_SUFFIXES
from manta_trading.data.tick.provider import ITickFileReader, TickFileDecodeError


class DuplicateDayError(ValueError):
    """Two data files of one job start on the same day."""


@dataclass(frozen=True)
class FileDays:
    by_day: dict[date, Path]
    unreadable: tuple[str, ...]
    """The reader's message (it names the file) for each refused header."""

    def unclaimed_reason(self) -> str:
        """The failure for a day no readable file claims, when any was refused."""
        return "header: no readable file for this day; refused: " + "; ".join(
            self.unreadable
        )


def file_days(paths: Iterable[Path], reader: ITickFileReader) -> FileDays:
    """Data files by header start day, and the refused ones. Blocking."""
    by_day: dict[date, Path] = {}
    unreadable: list[str] = []
    for path in paths:
        if not path.name.endswith(DATA_FILE_SUFFIXES):
            continue
        try:
            day = reader.open_file(path).start.date()
        except TickFileDecodeError as exc:
            unreadable.append(str(exc))  # the message names the file
            continue
        if day in by_day:
            raise DuplicateDayError(
                f"{path.name} and {by_day[day].name} both start on {day}"
            )
        by_day[day] = path
    return FileDays(by_day, tuple(unreadable))
