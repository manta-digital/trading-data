import unittest
import asyncio
from manta_trading.util.asynceventemitter import AsyncEventEmitter


class TestAsyncEventEmitter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.emitter = AsyncEventEmitter()

    async def test_on_and_emit(self):
        results = []

        def sync_handler(data):
            results.append(f"Sync: {data}")

        async def async_handler(data):
            await asyncio.sleep(0.1)  # Simulate async operation
            results.append(f"Async: {data}")

        self.emitter.on("test_event", sync_handler)
        self.emitter.on("test_event", async_handler)

        await self.emitter.emit("test_event", "Hello")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], "Sync: Hello")
        self.assertEqual(results[1], "Async: Hello")

    async def test_remove_listener(self):
        results = []

        def handler(data):
            results.append(data)

        self.emitter.on("test_event", handler)
        await self.emitter.emit("test_event", "First")
        self.emitter.remove_listener("test_event", handler)
        await self.emitter.emit("test_event", "Second")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "First")

    async def test_multiple_events(self):
        results = {}

        async def handler1(data):
            results['event1'] = data

        async def handler2(data):
            results['event2'] = data

        self.emitter.on("event1", handler1)
        self.emitter.on("event2", handler2)

        await self.emitter.emit("event1", "Data 1")
        await self.emitter.emit("event2", "Data 2")

        self.assertEqual(len(results), 2)
        self.assertEqual(results['event1'], "Data 1")
        self.assertEqual(results['event2'], "Data 2")

    async def test_emit_non_existent_event(self):
        # This should not raise an exception
        await self.emitter.emit("non_existent", "data")

    async def test_remove_non_existent_listener(self):
        def handler(data):
            pass

        # This should not raise an exception
        self.emitter.remove_listener("test_event", handler)
        
    async def test_error_handling_in_callbacks(self):
        results = []
        
        def failing_sync_handler(data):
            raise ValueError("Sync error")
            
        async def failing_async_handler(data):
            await asyncio.sleep(0.1)
            raise ValueError("Async error")
            
        def working_handler(data):
            results.append(f"Working: {data}")
        
        self.emitter.on("test_event", failing_sync_handler)
        self.emitter.on("test_event", failing_async_handler)
        self.emitter.on("test_event", working_handler)
        
        # This should not raise an exception due to our error handling
        emit_results, errors = await self.emitter.emit("test_event", "Test Data")
        
        # Verify that the working handler was still called
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "Working: Test Data")
        
        # Verify that we collected error information
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(e, ValueError) for e in errors))


if __name__ == '__main__':
    unittest.main()
