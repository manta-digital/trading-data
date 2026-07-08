from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Asynchronous rate limiter that controls call frequency within a time period.

    Limits callers to `max_calls` per `period` seconds. Uses a sliding-window
    approach: tracks exact timestamps of recent calls and waits until the oldest
    call expires before allowing the next one when at capacity.

    The lock is released during the sleep window so concurrent coroutines can
    check the rate limit independently rather than serializing on the lock itself.
    """

    def __init__(self, max_calls: int, period: float) -> None:
        self.max_calls = max_calls
        self.period = period
        self.calls: list[float] = []
        self.lock = asyncio.Lock()

    async def __aenter__(self) -> RateLimiter:
        while True:
            async with self.lock:
                now = time.time()
                # Prune calls outside the sliding window
                self.calls = [t for t in self.calls if now - t < self.period]

                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return self

                # At limit: compute wait time, then release lock before sleeping
                oldest_call = self.calls[0]
                time_to_wait = (oldest_call + self.period) - now

            # Sleep outside the lock so concurrent callers can proceed
            if time_to_wait > 0:
                await asyncio.sleep(time_to_wait)
            # Re-acquire lock and re-check capacity

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        pass

    async def acquire(self) -> RateLimiter:
        """Acquire a rate limit token (equivalent to entering the async context manager).

        Provided for compatibility with semaphore-like interfaces.
        """
        return await self.__aenter__()

    async def release(self) -> None:
        """No-op. Token lifecycle is managed automatically by the sliding window.

        Kept for backwards compatibility with semaphore-like interfaces.
        """
        pass
