import pickle
import time
from threading import Lock

import zmq
from repeatedtimer import RepeatedTimer


# zeroMQ receiver object.  Handles data received across ZeroMQ socket.  The ZeroMQ pairs
# function as the bridge that allows creating this using a simplified version of
# microservices idea.
class ZmqReceiver:

    def __init__(self):
        self.context = zmq.Context()
        self.poller = None
        self.pollingInterval = 1000
        self.heartbeatInterval = 10
        self.heartbeat = RepeatedTimer(self.heartbeatInterval, self.detectTimeout)
        self.socket = None
        self.connected = False
        self.addr = None
        self.lock = Lock()

    # Define our connection function.  Returns true on connect, False on timeout or error.
    # We want to call this until it returns true.  polling2 would take care of this but
    # we already have RepeatedFnTimer so use that.  Polling2 would run this until it
    # returned true, which is what we want.

    # flip and have this call detectTimeout, not the other way around
    def connect(self, addr):
        try:
            self.socket = self.context.socket(zmq.SUB)
            self.socket.setsockopt(zmq.SUBSCRIBE, b'META')
            self.socket.setsockopt(zmq.SUBSCRIBE, b'QUOTE')
            self.socket.setsockopt(zmq.RCVTIMEO, 5)

            self.addr = addr
            self.socket.connect(addr)

            self.initPoller()
            self.heartbeat.start()
            return self.context, self.socket, True

        except:
            return self.context, self.socket, False

    # This is ok but not incredibly useful as the poller doesn't seem to timeout so we
    # still have to implement that part ourselves.  Now we need to know if this has
    # received nothing for n seconds.  ZMQ doesn't seem to be *any* help here
    # if self.poller is not None:
    #     self.poller.unregister()
    #     self.poller = None
    def initPoller(self):
        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)

    # If this function is called we timed out.
    # none of this is true
    def detectTimeout(self):
        # print("Connection broken.  Not making decisions until data received")

        try:
            self.connected = False
            print("Connecting to marketlink data provider...")

            # don't forget to change this port as needed.  Ideally request a port from
            # marketlink, but that would require a connection...
            (self.context, self.socket, self.status) = self.connect(self.addr)
            if self.status:
                print("Connected.  Stopping connection timer")

            self.initPoller()

            self.lock.acquire()
            self.heartbeat.stop()
            self.heartbeat.start()
            self.lock.release()

        except zmq.ZMQError as e:
            if e.errno == zmq.EAGAIN:
                print("Socket appears to have timed out")

        except Exception as e:
            print(e)

    # main polling / query mechanism
    def poll(self):
        try:
            while True:
                # can do this, or should be able to use recv
                # message = self.socket.recv(zmq.DONTWAIT)
                # print("message: " + message)

                # don't forget to separate off topics
                # 'META'
                # 'QUOTE'
                # 'PING'

                # ZMQ poll function.  This does not repeatedly poll.
                socks = dict(self.poller.poll(self.pollingInterval))

                if self.socket in socks and socks[self.socket] == zmq.POLLIN:

                    # this is the only thing we're actually doing with the data.  printing it:
                    # message = socket.recv_pyobj()
                    [topic, _message] = self.socket.recv_multipart()
                    message = pickle.loads(_message)
                    print(f"{topic}: {message}")
                    # end of processing the data.

                    self.lock.acquire()
                    self.heartbeat.stop()
                    self.heartbeat.start()
                    self.lock.release()

                else:
                    print("waiting")

                # we poll and sleep for 1 second.
                time.sleep(1)

        except zmq.ZMQError as e:
            if e.errno == zmq.EAGAIN:
                print("Socket appears to have timed out")

        except Exception as e:
            print(e)


# Note: appears to need the full address not just //*:port.
def main():
    zmqReceiver = ZmqReceiver()

    # the reason have to run the sender first is that this only makes one attempt to connect.  this program would
    # probably be made considerably easier by Polly
    zmqReceiver.connect("tcp://127.0.0.1:5556")

    zmqReceiver.poll()
    input("press any key to end receiver test")


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
