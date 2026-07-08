import zmq

# MarketLinkReceiver just provides an interface
from marketlink import MarketLinkReceiver
from databaselink import DBError


# Accepts inbound data link from market service (MarketLink).  Responsible for receiving processed incoming
# data, publishing as needed, pushing to persistence (database).  This is *not* a ZMQ receiver.  This is used
# by main to connect the DatabaseLink and persist the streaming market data.
# todo: rename stuff because everything seems to be called Link.

class LinkReceiver(MarketLinkReceiver):

    def __init__(self, _db=None, _channels=None):
        self.initialPacket = False
        self.db = _db
        self.channels = _channels

    def onQuoteAvailable(self, dataFrame):
        try:
            if not self.initialPacket:
                self.initialPacket = True

            print("Link Receiver: ")
            print(dataFrame)
            print()

            if self.db is not None:
                self.db.addTickData(dataFrame)

        except Exception as e:
            print(f"Exception receiving quote: {e}")

    # When meta quote is received, ensure that the corresponding records are created in the database
    def onMetaQuoteAvailable(self, dataFrame):
        print("Metaquote Received:")
        print(dataFrame)

        # Does this belong in a linkReceiver class?  Because it feels a lot more like DatabaseLink
        # code.
        if self.db is not None:

            def addMetaSymbolRow(row):
                result = self.db.addMetaSymbol(
                    str(row['symbol']),
                    str(row['symbolActive']),
                    str(row['description']),
                    row['dateActive'],
                    row['dateExpire'],
                    row['dateRollover'],
                    row['tick'],
                    row['tickAmount'],
                    row['futureMultiplier'],
                    row['delayed'],
                    row['realTime'],
                    row['isActive']
                )

                return result

            def f(row):
                result = addMetaSymbolRow(row)

                # Symbol missing.  Make one attempt to create.
                if result == DBError.MISSING_FK:
                    status = self.db.addSymbol(
                        row['symbol'], row['assetType'], row['assetMainType'], row['exchange'])

                    print(f"Missing FK.  Adding symbol.  Result: {status}")

                    if status == DBError.SUCCESS:
                        result = addMetaSymbolRow(row)

                return result

            # apply function to each row.  Function will use data from the row and create metaSymbol in db.
            dataFrame.apply(f, axis=1)
