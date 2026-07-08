# main controller / driver for market-link first tasks
import csv
from dotenv import load_dotenv

from databaselink import DatabaseLink
from linkreceiver import LinkReceiver
from marketlink import Frequency, MarketLink

# Lab11: Backtesting.py initial test
# goal is to create *anything* using Backtesting.py as a starting point.  Just test static/historical
# data.  Ideally pull from DB with psycopg2, but can use CSV if needed.


def main():
    try:

        load_dotenv()

        # simple test how do formats work
        # n = 12311111
        # print('test format {:08X}'.format(n))
        # exit(0)

        # todo: set up a simple utility to start this thing up.
        # This will cause marketlink to pull quotes on the specified intervals and publish them so they can
        # be received by the receiver.  Marketlink will handle putting them into the DB.  Receiver is just
        # receiving so it can be plugged into a strategy or similar.
        # See lab8 for additional details
        marketLink = MarketLink()
        marketLink.addQuoteOnInterval(['/ES', '/GC'], frequency=Frequency.SECOND)

        # Set up a database link that the receiver object can use to communicate with our market database
        dbLink = DatabaseLink('marketdata')
        status = dbLink.connect()
        print(f"Database Connection status: {status}")

        # define an object to receive MarketLink's data publications.  LinkReceiver is added as a subscriber
        # of MarketLink's publication.
        receiver = LinkReceiver(dbLink)
        marketLink.addSubscriber(receiver)
        marketLink.start()

    # Receiving a threading/lock exception during testing.
    finally:
        pass
    #    if marketLink is not None: marketLink.stop()
    #    if dbLink is not None: dbLink.disconnect()


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
