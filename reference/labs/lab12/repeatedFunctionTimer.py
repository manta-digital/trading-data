from threading import Timer
from repeatedtimer import RepeatedTimer


# A repeated timer that uses a function evaluated at current time to determine its interval.
# Deprecated.  Use RepeatedThreadTimer instead.
class RepeatedFnTimer(RepeatedTimer):
    def __init__(self, intervalFn, function, *args, **kwargs):
        super(function, *args, **kwargs)
        self.interval = 0
        self.intervalFunction = intervalFn

    def _run(self):
        super()._run()

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.intervalFunction(), self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        super().stop()


def main():
    def stopTimers():
        t1.stop()
        print("-------------- timers stopped! ----------------------")

    def getInterval(x):
        return 2.0

    t1 = RepeatedFnTimer(getInterval, lambda: print("Timer 1"))

    t1.start()
    print("-------------- timers are running -------------------")


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
