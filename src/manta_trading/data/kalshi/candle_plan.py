"""Batch planner for the candle phase — pure (slice 264, Decision 3).

No I/O, no SQL, no clock: every input is an argument, so the planner is
tested exhaustively without a database or a client. Two facts from the
design's Discovery Findings shape it: the batch endpoint caps a request on
``len(tickers) × periods_in_window`` (``CANDLE_BATCH_MAX_CANDLES``) and on
``len(tickers)`` (``CANDLE_BATCH_MAX_TICKERS``), computed on what is *asked
for*, not on the sparse handful of candles that come back. ``plan_batches``
guarantees both caps on every batch it returns, which is why a 400 on the
batch path is a planner bug (Decision 7).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil

from manta_trading.data.kalshi.constants import CandlePeriod


@dataclass(frozen=True)
class PendingMarket:
    """One row of a pending query: the market plus its candle state, if any.

    ``watermark_ts`` is ``None`` when no ``market_candle_state`` row exists —
    the market has never been requested (Decision 5 decides its start).
    """

    ticker: str
    open_time: datetime
    close_time: datetime
    watermark_ts: datetime | None


@dataclass(frozen=True)
class CandleTarget:
    """A market's fetch window ``[start, end)`` for this pass.

    ``close_end`` is ``close_time + period`` — the last candle Kalshi serves
    ends one period after close (Discovery Findings), so it is both the
    window's ceiling and the watermark's.
    """

    ticker: str
    start: datetime
    end: datetime
    close_end: datetime


@dataclass(frozen=True)
class CandleBatch:
    """One ``GET /markets/candlesticks`` request: these tickers, this window."""

    tickers: tuple[str, ...]
    start: datetime
    end: datetime


def period_span(period: CandlePeriod) -> timedelta:
    return timedelta(minutes=int(period))


def periods_in(start: datetime, end: datetime, period: CandlePeriod) -> int:
    """Periods a request for ``[start, end)`` asks the endpoint for.

    Measured live: the cap counts ``(end - start) / period`` (100 tickers ×
    a 360-minute window was refused as 36,000). Rounded up so an unaligned
    ``start`` (an ``open_time`` with seconds) never under-counts.
    """
    return ceil((end - start) / period_span(period))


def last_complete_period(now: datetime, period: CandlePeriod) -> datetime:
    """``floor(now, period) − period`` — the newest period whose candle has
    fully settled (Decision 3's one-period guard for a still-settling candle
    in a conflict-ignore table). Aligned to the Unix epoch, as Kalshi's
    ``end_period_ts`` values are.
    """
    span = int(period_span(period).total_seconds())
    floored = int(now.timestamp()) // span * span
    return datetime.fromtimestamp(floored - span, tz=UTC)


def target_window(
    market: PendingMarket,
    *,
    phase_start: datetime,
    period: CandlePeriod,
    lookback: timedelta,
) -> CandleTarget | None:
    """The window to fetch for ``market`` this pass, or ``None`` if nothing.

    ``start`` is the watermark when a state row exists (the request
    re-includes the watermark instant — boundary inclusivity is
    undocumented and the overlap is free under conflict-ignore); otherwise
    ``max(open_time, min(close_time, phase_start) − lookback)`` (Decision 5:
    first sight buys ``lookback`` of history, a market seen young starts at
    its open). ``end = min(close_time + period, last_complete_period)``.
    """
    close_end = market.close_time + period_span(period)
    end = min(close_end, last_complete_period(phase_start, period))
    if market.watermark_ts is not None:
        start = market.watermark_ts
    else:
        start = max(market.open_time, min(market.close_time, phase_start) - lookback)
    if start >= end:
        return None
    return CandleTarget(market.ticker, start, end, close_end)


def plan_batches(
    targets: Iterable[CandleTarget],
    *,
    period: CandlePeriod,
    max_tickers: int,
    max_candles: int,
) -> list[CandleBatch]:
    """Pack targets into requests under both caps; deterministic.

    Any single target longer than ``max_candles`` periods is first split into
    consecutive windows that tile its range. Targets are then sorted by
    ``start`` and packed greedily: a target joins the open batch only while
    ``len + 1 ≤ max_tickers`` and ``(len + 1) × periods(union) ≤ max_candles``
    — the union window is what the request asks for, so a distant target can
    overflow the candle cap even when the ticker count is small. Packing may
    widen a ticker's request (conflict-ignore makes that free); it never
    drops a period.
    """
    pieces = sorted(
        (
            piece
            for target in targets
            for piece in _split_over_long(target, period, max_candles)
        ),
        key=lambda t: (t.start, t.end, t.ticker),
    )
    batches: list[CandleBatch] = []
    open_batch: list[CandleTarget] = []
    for piece in pieces:
        fits = _admits(open_batch, piece, period, max_tickers, max_candles)
        if open_batch and not fits:
            batches.append(_close(open_batch))
            open_batch = []
        open_batch.append(piece)
    if open_batch:
        batches.append(_close(open_batch))
    for batch in batches:
        _check_caps(batch, period, max_tickers, max_candles)
    return batches


def _split_over_long(
    target: CandleTarget, period: CandlePeriod, max_candles: int
) -> list[CandleTarget]:
    span = period_span(period)
    pieces: list[CandleTarget] = []
    start = target.start
    while periods_in(start, target.end, period) > max_candles:
        end = start + span * max_candles
        pieces.append(CandleTarget(target.ticker, start, end, target.close_end))
        start = end
    pieces.append(CandleTarget(target.ticker, start, target.end, target.close_end))
    return pieces


def _admits(
    open_batch: list[CandleTarget],
    piece: CandleTarget,
    period: CandlePeriod,
    max_tickers: int,
    max_candles: int,
) -> bool:
    if not open_batch:
        return True
    count = len(open_batch) + 1
    if count > max_tickers:
        return False
    union_start = min(open_batch[0].start, piece.start)
    union_end = max(max(t.end for t in open_batch), piece.end)
    return count * periods_in(union_start, union_end, period) <= max_candles


def _close(open_batch: list[CandleTarget]) -> CandleBatch:
    return CandleBatch(
        tickers=tuple(t.ticker for t in open_batch),
        start=min(t.start for t in open_batch),
        end=max(t.end for t in open_batch),
    )


def _check_caps(
    batch: CandleBatch, period: CandlePeriod, max_tickers: int, max_candles: int
) -> None:
    # Decision 7: a request over either cap would draw a 400 from the
    # endpoint; that is this module's bug, so it is refused here, loudly,
    # and never sent. An explicit raise, not ``assert`` — ``-O`` must not
    # strip the guard.
    count = len(batch.tickers)
    requested = count * periods_in(batch.start, batch.end, period)
    if count > max_tickers or requested > max_candles:
        raise AssertionError(
            f"planner produced an over-cap batch: {count} tickers × "
            f"{periods_in(batch.start, batch.end, period)} periods = {requested} "
            f"(caps {max_tickers} tickers, {max_candles} candles)"
        )
