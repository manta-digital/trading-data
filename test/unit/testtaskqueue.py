import unittest
import asyncio
from manta_trading.tasks.taskqueue import TaskQueue, Task


class TaskQueueTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.task_queue = TaskQueue(max_concurrent=2)

    async def dummy_task(self, duration, result):
        await asyncio.sleep(duration)
        return result

    async def test_add_and_get_task(self):
        task = Task(coroutine=self.dummy_task, args=(1, "test"), priority=1)
        await self.task_queue.addTask(task)
        fetched_task = await self.task_queue.getTask()

        self.assertEqual(fetched_task.coroutine, self.dummy_task)
        self.assertEqual(fetched_task.args, (1, "test"))
        self.assertEqual(fetched_task.priority, 1)

    async def test_process_queue_executes_tasks(self):
        results = []

        async def sample_task(duration, result):
            await asyncio.sleep(duration)
            results.append(result)

        task1 = Task(coroutine=sample_task, args=(0.1, "task1"), priority=1)
        task2 = Task(coroutine=sample_task, args=(0.1, "task2"), priority=2)

        await self.task_queue.addTask(task1)
        await self.task_queue.addTask(task2)

        processor_task = asyncio.create_task(self.task_queue.processQueue())

        await asyncio.sleep(0.3)  # Allow some time for tasks to be processed

        self.assertIn("task1", results)
        self.assertIn("task2", results)

        processor_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            pass
    
    async def test_concurrent_task_failures(self):
        """Test that active_tasks counter remains consistent when tasks fail concurrently"""
        async def failing_task():
            raise RuntimeError("Task intentionally failed")
        
        # Add multiple failing tasks
        for _ in range(5):
            task = Task(coroutine=failing_task, priority=1)
            await self.task_queue.addTask(task)
        
        # Process all tasks and let them fail
        processor_task = asyncio.create_task(self.task_queue.processQueue())
        await asyncio.sleep(0.5)  # Give some time for all tasks to fail
        
        # Check that active_tasks is properly decremented
        self.assertEqual(self.task_queue.active_tasks, 0)
        
        processor_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    unittest.main()
