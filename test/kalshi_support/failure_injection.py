"""``FailureInjection`` — the ``raise_on``/``_record`` engine the kalshi
fakes share (267 code review: it had been copied verbatim between
``FakeTradeSource`` and ``FakeHistoricalSource``). A fake inherits it and
calls ``_record`` at the top of every faked method; a test scripts failures
with ``raise_on``. ``calls`` keeps every recorded method name in order.
"""

from __future__ import annotations

from collections.abc import Callable


class FailureInjection:
    """See the module docstring."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._failures: list[
            tuple[
                str,
                BaseException,
                int | None,
                Callable[[dict[str, object]], bool] | None,
            ]
        ] = []
        self._counts: dict[str, int] = {}

    def raise_on(
        self,
        call: str,
        exc: BaseException,
        *,
        at: int | None = None,
        when: Callable[[dict[str, object]], bool] | None = None,
    ) -> None:
        """Raise ``exc`` on the ``at``-th invocation of ``call`` and/or when
        ``when(query)`` is true (``call`` is the method name)."""
        self._failures.append((call, exc, at, when))

    def _record(self, call: str, query: dict[str, object]) -> None:
        self.calls.append(call)
        self._counts[call] = self._counts.get(call, 0) + 1
        for name, exc, at, when in self._failures:
            if name != call:
                continue
            if at is not None and self._counts[call] != at:
                continue
            if when is not None and not when(query):
                continue
            raise exc
