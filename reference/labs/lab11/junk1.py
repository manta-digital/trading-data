def packetTest(packet, useBinary=False):
    if packet.startswith('hello'):
        pass

    return packet


def ordTest(packet):
    size0 = ord(packet[0])
    size1 = ord(packet[1])
    print(f"Size0: {size0}, Size1: {size1}")


def chrTest(packet):
    size = len(packet)
    pageHigh = size & 0xFF00
    pageHighShift = pageHigh >> 8
    pageLow = size & 0x00FF

    chrHigh = chr(pageHighShift)
    chrLow = chr(pageLow)

    packetSize = chrHigh + chrLow
    return packetSize

    # length is only padding to intervals of 16 that is fine
    # so can we assume they will come in that way? probably.

    # todo: make sure good on this chr/ord stuff


def main():
    try:
        packets = {
            '531F00021A2C010F',
            '531F00021A000000'
        }

        #ordTest('01test')
        #ordTest('AB')

        for p in packets:
            chrTest(p)

    finally:
        pass



# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)

