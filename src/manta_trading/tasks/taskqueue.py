import asyncio

from collections.abc import Callable
from manta_trading.logging import get_logger
from typing import Any, Optional

_logger = get_logger(__name__)
from dataclasses import dataclass, field


# order=True will tell Python to generate comparison methods based on the priority field.
@dataclass(order=True)
class Task:
    priority: int
    coroutine: Callable[..., Any] = field(compare=False)
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    result: Optional[Any] = field(default=None, compare=False)


class TaskQueue:
    def __init__(self, max_concurrent=10):
        self.queue = asyncio.PriorityQueue()
        self.max_concurrent = max_concurrent
        self.active_tasks = 0
        self.task_lock = asyncio.Lock()  # Lock to protect active_tasks counter
        self.running_tasks = set()

    async def addTask(self, task: Task):
        await self.queue.put((task.priority, task))

    async def getTask(self) -> Optional[Task]:
        if self.queue.empty():
            return None
        _, task = await self.queue.get()
        return task

    async def processQueue(self):
        while True:
            if self.active_tasks < self.max_concurrent:
                task = await self.getTask()
                if task is None:
                    await asyncio.sleep(0.1)  # Prevent busy-waiting
                    continue
                asyncio_task = asyncio.create_task(self.runTask(task))
                self.running_tasks.add(asyncio_task)
                asyncio_task.add_done_callback(self.running_tasks.discard)
            else:
                await asyncio.sleep(0.1)

    async def runTask(self, task):
        # Use lock to safely modify active_tasks counter
        async with self.task_lock:
            self.active_tasks += 1
        
        try:
            result = await task.coroutine(*task.args, **(task.kwargs or {}))
            if result is None:
                _logger.warning("Task %s returned None", task)
                task.result = {'error': 'Task returned None'}
            elif isinstance(result, dict) and 'error' in result:
                _logger.warning("Task %s returned an error: %s", task, result['error'])
                task.result = result
            else:
                task.result = result
        except Exception as e:
            _logger.error("Error processing task: %s", e)
            task.result = {'error': str(e)}
        finally:
            # Use lock again to safely decrement active_tasks counter
            async with self.task_lock:
                self.active_tasks -= 1
            self.queue.task_done()

    def runTaskProcessor(self):
        return self.processQueue()
