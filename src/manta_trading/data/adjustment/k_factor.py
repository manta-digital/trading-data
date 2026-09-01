"""Pure k-factor computation for split/dividend adjustment.

The k-factor for a given (symbol, target_date) is the cumulative multiplier
that converts a raw closing price on ``target_date`` into the EODHD-style
``adjusted_close``::

    adjusted_close = raw_close * compute_k_factor(symbol, target_date, ...)

It is the product of contributions from every corporate action whose
``ex_date`` is strictly after ``target_date``:

* **Split** with ratio ``ratio_to / ratio_from`` (e.g. 4/1 for a 4-for-1)
  contributes ``ratio_from / ratio_to`` (= 1/4).
* **Cash dividend** of ``amount`` paid on ``ex_date`` contributes
  ``(prev_close - amount) / prev_close`` where ``prev_close`` is the closing
  price on the most recent trading day **before** ``ex_date``.

Both contributions are commutative under multiplication, so iteration order
does not matter as long as each action is applied exactly once.

The function is pure: it does no I/O. Callers (writer, verifier) are
responsible for fetching the splits, dividends, and prev-close data from
their respective sources before invoking this function.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Split:
    """Stock-split corporate action.

    Mirrors the ``splits`` table columns from migration ``003_splits``.

    A 4-for-1 split has ``ratio_to=4, ratio_from=1`` — every existing share
    becomes four shares. The price multiplier is ``ratio_from / ratio_to``.
    """

    symbol: str
    ex_date: date
    ratio_to: Decimal
    ratio_from: Decimal


@dataclass(frozen=True)
class Dividend:
    """Cash-dividend corporate action.

    Mirrors the ``dividends`` table columns from migration ``004_dividends``.
    ``amount`` is in ``currency`` (defaults to USD upstream); k-factor math
    assumes the ``amount`` and ``prev_close`` lookup are in the same currency.
    """

    symbol: str
    ex_date: date
    amount: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class CaSnapshot:
    """Frozen bundle of corporate-action data for one symbol.

    Loaded once per ingest pass per symbol via ``current_ca_snapshot()`` and
    passed to ``compute_k_factor`` for every target_date in that pass.

    ``frozen=True`` prevents field reassignment (not to enable hashing — the
    ``prev_closes`` dict field makes instances non-hashable; ``hash()`` raises
    ``TypeError`` by design since no caller needs ``CaSnapshot`` as a dict key
    or set member). This contract is pinned by ``test_casnapshot_not_hashable``.

    ``snapshot_id`` is computed at construction time by ``compute_snapshot_id``
    and is never ``None``.
    """

    symbol: str
    splits: tuple[Split, ...]
    dividends: tuple[Dividend, ...]
    prev_closes: dict[date, Decimal]
    snapshot_id: str


def compute_snapshot_id(
    splits: Iterable[Split], dividends: Iterable[Dividend]
) -> str:
    """Return a stable SHA256 hex digest over ``(splits, dividends)``.

    The digest is deterministic across processes and Python restarts. It keys
    on corporate-action identity only — ``(ex_date, ratio_to, ratio_from)``
    for splits and ``(ex_date, amount)`` for dividends.  ``fetched_at`` is
    intentionally excluded: the ingest path bumps ``fetched_at`` on every
    upsert cycle regardless of whether the underlying ratio/amount changed,
    so including it would cause spurious snapshot changes and unnecessary
    band-based recomputes in slice 144's daemon.
    """
    splits_canon = sorted(
        (s.ex_date.isoformat(), str(s.ratio_to), str(s.ratio_from))
        for s in splits
    )
    dividends_canon = sorted(
        (d.ex_date.isoformat(), str(d.amount))
        for d in dividends
    )
    payload = json.dumps(
        {"splits": splits_canon, "dividends": dividends_canon},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_k_factor(
    symbol: str,
    target_date: date,
    splits: Iterable[Split] | None = None,
    dividends: Iterable[Dividend] | None = None,
    prev_closes: Mapping[date, Decimal] | None = None,
    *,
    ca_snapshot: CaSnapshot | None = None,
) -> Decimal:
    """Compute the cumulative k-factor for ``(symbol, target_date)``.

    Accepts either the positional-arg form (backward-compatible with the old
    ``k_factor`` name) or a ``ca_snapshot`` keyword argument.  Both paths
    execute the same internal math.

    Positional form (legacy / test fixtures)::

        compute_k_factor(symbol, target_date, splits, dividends, prev_closes)

    Preferred form (slice 143+)::

        compute_k_factor(symbol, target_date, ca_snapshot=snap)

    Returns ``Decimal('1')`` when no corporate actions exist after
    ``target_date``. Dividend amounts and ``prev_closes`` values must be
    ``Decimal`` instances; the function does not coerce floats to preserve
    precision.

    Raises :class:`KeyError` if a dividend's ``prev_close`` is missing from
    ``prev_closes`` / ``ca_snapshot.prev_closes``. Callers must populate the
    map for every dividend ``ex_date`` they pass in.
    """
    if ca_snapshot is not None:
        _splits: Iterable[Split] = ca_snapshot.splits
        _dividends: Iterable[Dividend] = ca_snapshot.dividends
        _prev_closes: Mapping[date, Decimal] = ca_snapshot.prev_closes
    else:
        if splits is None or dividends is None or prev_closes is None:
            raise TypeError(
                "compute_k_factor requires either ca_snapshot or all three of "
                "splits, dividends, and prev_closes"
            )
        _splits = splits
        _dividends = dividends
        _prev_closes = prev_closes

    k = Decimal("1")

    for split in _splits:
        if split.symbol != symbol:
            continue
        if split.ex_date <= target_date:
            continue
        if split.ratio_to <= 0 or split.ratio_from <= 0:
            raise ValueError(
                f"split for {symbol} on {split.ex_date} has non-positive ratio: "
                f"{split.ratio_from}/{split.ratio_to}"
            )
        k *= split.ratio_from / split.ratio_to

    for div in _dividends:
        if div.symbol != symbol:
            continue
        if div.ex_date <= target_date:
            continue
        prev_close = _prev_closes.get(div.ex_date)
        if prev_close is None:
            raise KeyError(
                f"prev_close missing for dividend {symbol} ex_date={div.ex_date}; "
                "caller must supply close on the most recent trading day "
                "before ex_date"
            )
        if prev_close <= 0:
            raise ValueError(
                f"prev_close for {symbol} on {div.ex_date} is non-positive: "
                f"{prev_close}"
            )
        k *= (prev_close - div.amount) / prev_close

    return k
