import threading
import time
import queue
from threading import Thread


# Created by GPT4.  This is a decorator that limits the number of calls to a function per minute.
# Call wrapper enquees the function request, execute pulls requests according to the rate limit.
class RateLimiter:
    def __init__(self, max_per_minute):
        self.interval = 60.0 / max_per_minute
        self.last_call = time.time()
        self.call_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None

    def __call__(self, func):
        def wrapper(*args, **kwargs):
            self.call_queue.put((func, args, kwargs))
            return None

        return wrapper

    def execute(self):
        while not self.stop_event.is_set():
            if not self.call_queue.empty():
                func, args, kwargs = self.call_queue.get()
                now = time.time()
                if now - self.last_call < self.interval:
                    time.sleep(self.interval - (now - self.last_call))
                self.last_call = time.time()
                func(*args, **kwargs)
            else:
                time.sleep(self.interval)

    def start(self):
        self.thread = Thread(target=self.execute)
        self.thread.daemon = True
        self.thread.start()
        self.stop_event.clear()

    def stop(self):
        self.stop_event.set()
        self.thread.join()


if __name__ == "__main__":
    rateLimiter = RateLimiter(10)

    @rateLimiter
    def test():
        print("test")

    rateLimiter.start()
    test()
    test()
    test()
    test()

    input("Press any key to exit...")
