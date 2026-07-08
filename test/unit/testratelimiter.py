import unittest
import asyncio
import time
from manta_trading.util.ratelimiter import RateLimiter


class RateLimiterTest(unittest.IsolatedAsyncioTestCase):

    async def test_acquire_allows_calls_within_rate(self):
        rate_limiter = RateLimiter(max_calls=4, period=8)

        start_time = time.time()
        async with rate_limiter:
            pass
        async with rate_limiter:
            pass
        elapsed_time = time.time() - start_time

        self.assertLess(elapsed_time, 1)

    async def test_acquire_limits_calls_exceeding_rate(self):
        rate_limiter = RateLimiter(max_calls=4, period=8)

        start_time = time.time()

        # First 4 calls should not be delayed
        for _ in range(4):
            async with rate_limiter:
                pass

        # This 5th call should be delayed
        async with rate_limiter:
            pass

        elapsed_time = time.time() - start_time

        # The 5th call should be delayed by about 8 seconds
        self.assertGreaterEqual(elapsed_time, 8)
        self.assertLess(elapsed_time, 8.5)  # Allow some small margin for execution time

    async def test_multiple_concurrent_requests(self):
        rate_limiter = RateLimiter(max_calls=4, period=8)

        async def make_request():
            async with rate_limiter:
                await asyncio.sleep(0.1)  # Simulate some work

        start_time = time.time()
        await asyncio.gather(*[make_request() for _ in range(5)])
        elapsed_time = time.time() - start_time

        # We expect this to take at least 1 second due to rate limiting
        self.assertGreaterEqual(elapsed_time, 1)

if __name__ == '__main__':
    unittest.main()
