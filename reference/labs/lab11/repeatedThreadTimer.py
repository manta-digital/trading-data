import random
# Attempt to create repeating function on an interval without using a new thread
# every time we run the timer.  In practice the 'thread per exec' timers seem to
# run ok but are difficult to debug and may cause issues with extended runs.

# there seems to be a zillion ways to do this:
# https://stackoverflow.com/questions/474528/how-to-repeatedly-execute-a-function-every-x-seconds/25251804#25251804


# timed_count module: https://pypi.org/project/timed-count/
# using sched may be a decent possibility as well (import sched)

# This attempt will use the accepted answer from the above SO link.

# This works but have neither checked for drift nor done an interval function of this one yet.
from threading import Timer
from threading import Thread
from threading import Event


class RepeatedThreadTimer(Thread):

    def __init__(self, intervalFunction, function, *args, **kwargs):
        Thread.__init__(self)
        self.function = function
        self.intervalFunction = intervalFunction
        self.args = args
        self.kwargs = kwargs
        self.stopped = Event()

    def getEvent(self):
        return self.stopped

    def run(self):
        while not self.stopped.wait(self.intervalFunction()):
            self.function(*self.args, **self.kwargs)

    def start(self):
        self.stopped.clear()
        super().start()

    def stop(self):
        self.stopped.set()

def main():

    def timerStop():
        timer.stop()

    stopTimer = Timer(random.uniform(5.0, 10.0), timerStop)
    stopTimer.start()

    def printFn():
        print("hello")

    timer = RepeatedThreadTimer(lambda: 0.2, printFn)
    timer.start()

# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)