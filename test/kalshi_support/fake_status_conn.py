"""``FakeStatusConn`` — a synchronous psycopg connection stand-in for the
``status`` readers (slice 267, Task 7.3).

The readers issue only ``execute(query, params).fetchone()``. A
``sync_state`` read is answered by the row registered for
``params["surface"]``; any other statement (the trade counts aggregate) by
``counts``. Every ``params`` mapping is recorded so a test can assert what
was bound — the effective floor, in particular.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import psycopg


class _Cursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakeStatusConn:
    def __init__(
        self,
        rows_by_surface: Mapping[str, tuple[Any, ...] | None],
        *,
        counts: tuple[Any, ...] | None = None,
    ) -> None:
        self._rows = dict(rows_by_surface)
        self._counts = counts
        self.params: list[dict[str, Any]] = []

    def as_connection(self) -> psycopg.Connection[Any]:
        """The readers are typed over a real connection; this is the one
        structural seam they use."""
        return cast(psycopg.Connection[Any], self)

    def execute(self, query: Any, params: Mapping[str, Any]) -> _Cursor:
        recorded = dict(params)
        self.params.append(recorded)
        if "surface" in recorded:
            return _Cursor(self._rows.get(recorded["surface"]))
        return _Cursor(self._counts)
