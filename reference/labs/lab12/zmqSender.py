# zeroMQ sender: use zeroMQ to publish data.  See if this is worth doing in Python.
import pickle
import time
import zmq


class ZmqSender:

    def __init__(self, addr):
        self.addr = addr
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(self.addr)
        #self.socket.bind("tcp://127.0.0.1:5556")

    def sendTestPacket(self, i):
        #  Send reply back to client
        topic = 42
        data = f"Here is some data ({i})".encode('UTF-8')

        print(f"Sending: {i}")
        self.socket.send(data)

    def sendPacketPyobj(self):
        dataList = [0, 1, 27, 42, 354]
        self.socket.send_pyobj(dataList)

    def sendTopicPacketPyobj(self, i):
        print(f"Sending multipart with topics ({i})")
        dataList = [0, 1, 27, 42, 354]
        dataList2 = [400, 327, 5]
        self.socket.send_multipart([b'ALL', pickle.dumps(dataList)])
        self.socket.send_multipart([b'SPECIAL', pickle.dumps(dataList2)])

    # This is the function that marketlink is currently calling
    def sendDataFrame(self, dataFrame):
        self.socket.send_multipart([b'QUOTE', pickle.dumps(dataFrame)])
        #self.socket.send_pyobj(dataFrame)


def main():
    try:
        i = 0
        zmqSender = ZmqSender("tcp://127.0.0.1:5556")
        while True:
            # This is the sender.  We want it to send stuff out (of course).  Ideally this will be data
            # received from linkreceiver.  It can be binary or json.  If binary we should probably
            # use messagepack.  So we will do a send with no blocking (send whether anyone is
            # listening or not).

            #  Send reply back to client
            i += 1
            zmqSender.sendTopicPacketPyobj(i)
            #zmqSender.sendPacketPyobj()

            #  Do some 'work'
            time.sleep(1)

    except Exception as e:
        print(e)


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
