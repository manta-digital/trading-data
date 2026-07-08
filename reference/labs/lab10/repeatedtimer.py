from threading import Timer


# update this so interval is a function, just have to make it work with a decent default
# would be nice to make the interval function optional.  will make it easy for marketlink
# to use it.  need to work on this a decent amount tomorrow  (old comment pre 2023)

# Repeating timer from this link: https://stackoverflow.com/questions/474528/what-is-the-best-way-to-repeatedly-execute-a-function-every-x-seconds/25251804
# #25251804
class RepeatedTimer(object):

    # UPDATE to use interval function.  allow specifying static interval also
    # sort out these arguments
    # then use tradingschedule function to get interval
    # also let tradingschedule specify if there is a delay
    # 
    def __init__(self, interval, function, *args, **kwargs):
        self._timer = None
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        if self._timer is not None:
            self._timer.cancel()

        self.is_running = False

    def setInterval(self, interval):
        self.interval = interval


def main():
    def stopTimers():
        t1.stop()
        t2.stop()
        t3.stop()
        t30.stop()
        print("-------------- timers stopped! ----------------------")

    t1 = RepeatedTimer(1.0, lambda: print("Timer 1"))
    t2 = RepeatedTimer(0.5, lambda: print("Timer 2"))
    t3 = RepeatedTimer(5.0, lambda: print("Timer 5!"))
    t30 = RepeatedTimer(30.0, stopTimers)

    t1.start()
    t2.start()
    t3.start()
    t30.start()
    print("-------------- timers are running -------------------")


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
