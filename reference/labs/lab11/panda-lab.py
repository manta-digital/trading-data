
import pandas as pd
from dotenv import load_dotenv
from marketlink import Frequency, MarketLink


# Creates dataframes from market data stream.  Goal is to test pandas dataframe joining.
class PandaLab:
    def __init__(self):
        self.dataBuffer = pd.DataFrame
        self.marketLink = None
        self.started = False
        pass

    # Start or restart pulling data.  This uses a full marketlink, which probably does WAY too much
    # stuff.
    def start(self):
        self.marketLink = MarketLink()

        # Use a marketlink but don't connect it to anything.  We will just intercept it here and use it to test
        marketLink = MarketLink()
        marketLink.addQuoteOnInterval(['/ES', '/GC'], frequency=Frequency.SECOND)
        marketLink.start()

        # what we really want is a raw quote service that we can do whatever we want with.



# Load environment, create the "pandalab" data fetcher and start its market link.  This will fetch data
# but doesn't make it very useful because we have no real receiver for it. we really barely want the
# marketlink we just want something really simple that will send us data.  we don't need duplicate filtering
# or anything.
#
# It's almost like a simpler MarketLink would work better...
# what should be done:
#
# split up marketlink and especially some of its functions.  we need something that can just give us raw
# data from the API but as dataframe.  one thing should just do that.  Processing even duplicate removal
# should be split up.
#
# So first, write something that can just hit the API and return the futures quote as a dataframe.
# then write its processing separately.




def main():
    try:
        load_dotenv()
        pandaLab = PandaLab()
        pandaLab.start()


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
