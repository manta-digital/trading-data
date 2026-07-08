# Market data "lunk" object (intended to become service at some point)
import json
import pandas as pd

from enum import Enum
from dotenv.main import load_dotenv
from repeatedThreadTimer import RepeatedThreadTimer
from tdapi import TDAPI
from futuresQuote import FuturesQuote
from zmqSender import ZmqSender

from datetime import datetime


# What should this object do?  Allow using a market API to pull data for specified symbol(s) on their
# respective schedules.  It can form into an API bridge but since there aren't multiple APIs set up yet,
# have it start out as a semi-smart service for getting market data and returning it in dataframes
#
# functions?
#   getQuote                    api already has a getQuote.  This could be api-agnostics but we only have one api for now...
#   getQuote(symbol, interval)  get quote and update every (interval)
#
# Ideally support different intervals for different signals.  Better timer loop would be extremely useful.  What we don't want
# is a different interval for everything.  Support a few types of intervals and group everything into those.
#
# Also, some intervals become unimportant.  For example, retrieving additional quotes when markets are closed.  Ideally would
# have smart intervals based on symbol, exchange, and desired frequency (tick, minute, day, etc)

# Questions:
# Should this incorporate a model?  Use a model?  Have any relation to a model?


# Multiples of a frequency (ex: 5 minute chart) are represented exactly that way: frequency = 3, n = 5.
class Frequency(Enum):
    NONE = 0,
    TICK = 1,
    SECOND = 2,
    MINUTE = 3,
    HOUR = 4,
    DAY = 5,
    WEEK = 6,
    MONTH = 7


defaultIntervals = {
    Frequency.NONE: 0,
    Frequency.TICK: 500,
    Frequency.SECOND: 1000,
    Frequency.MINUTE: 60000,
    Frequency.HOUR: 3600000,
    Frequency.DAY: 86400000,
    Frequency.WEEK: 604800000,
    Frequency.MONTH: 2592000000
}


# Informal receiver "interface"
class MarketLinkReceiver:
    def __init__(self):
        pass

    def onQuoteAvailable(self, dataFrame):
        pass

    def onMetaQuoteAvailable(self, dataFrame):
        pass


# Can set up several timers with threading.time.  For now just being able to get a reasonable timer interval is fine.
# 1) https://medium.com/codex/we-shouldve-just-used-python-c0a86470e558

# add timers for CME tick and regular exchange minute.  might be able to combine, but will need to be able to get market hours.
# more specifically, given a time, determine if the appropriate market is open.  If it is not open, determine the time interval
# between specified time and when it is open.

# ideally we can subscribe to the results of this class.
class MarketLink:

    # DOCUMENT THIS BETTER!!!
    # symbolsInit:      true if symbols have been primed / initialized
    # isRunning:        true if timers are running
    # tickSpeed:        fastest tick speed (defaults to 500 msec)
    # frequency:        timer frequency  Can have different frequencies for different symbols (ideally groups of symbols)
    # frequencyN:       number of frequency multiples for timers (ex: 30 seconds, 5 minutees)
    # symbols:          list of symbols for which quotes will be received.
    # api:              data-provider API abstraction

    # subscribers:              list of objects subscribing to notifications from this objhect.
    # receiverOnDatAMethod:     receiver method to call when quote is available
    # receiverOnMetaDataMethod: receiver method to call when metadata is available
    # dataBuffer:               precursor to model, just stores previous dataframe for comparison

    def __init__(self, schedule=None):
        self.symbolsInit = False
        self.isRunning = False
        self.tickSpeed = 500
        self.frequency = Frequency.TICK
        self.frequencyN = 1
        self.symbols = list()
        self.timers = dict()
        self.eventStop = None
        self.api = TDAPI()
        self.schedule = schedule
        self.address = "tcp://127.0.0.1:5556"

        self.subscribers = list()
        self.receiverOnDataMethod = 'onQuoteAvailable'
        self.receiverOnMetaDataMethod = 'onMetaQuoteAvailable'
        self.dataBuffer = pd.DataFrame()

        self.api.loadClientEnv()  # questionable to do this here in Python?
        self.useZmq = True
        self.zmqSender = ZmqSender(self.address)

    # Add symbol to specified timer
    # for now, hard coded just using the frequency of the first symbol added
    # THIS IS A POTENTIAL BUG.   Should be self.timers?
    #
    #def addSymbol(self, symbol, frequency=Frequency.MINUTE, n=1):
    #    if not self.symbols.hasKey(symbol):
    #        self.symbols[symbol] = {'frequency': (defaultIntervals[frequency] * n) / 1000}
     #       pass

    # Set receiver functions to be called when data is available.  Throw exception if receiver
    # does not provide the required function signature
    def addSubscriber(self, receiver):
        if not hasattr(receiver, self.receiverOnDataMethod) or not hasattr(receiver, self.receiverOnMetaDataMethod):
            print(f"MarketLink error -- cannot add receiver as subscriber")
            raise AttributeError(receiver)

        elif receiver in self.subscribers:
            print(f"MarketLink error -- receiver already registered, no action taken")

        else:
            self.subscribers.append(receiver)

    def unsubscribe(self, receiver):
        if receiver is not None and receiver in self.subscribers:
            self.subscribers.remove(receiver)

        else:
            print(f"MarketLink error -- cannot remove receiver {receiver}")

    # Add ZMQ receiver that will receive over ZMQ and not be called in direct, tightly-coupled mode.
    def addZMQSubscriber(self):
        pass

    # Simple function to retrieve quote for particular symbol on the specified interval.
    # uses timer quoteTimer for this method (only one symbol monitored this way is supported
    # at a time).
    #
    # symbol: symbols to pull, may be a collection/iterable
    # frequency: basic time frequency
    # n: multiple of frequency (ex: 5 with frequency SECONDS = every 5 seconds)
    # equityType: used as default scheduler grouping
    # scheduleGroup: optional schedule grouping to create a different time frequency
    def addQuoteOnInterval(self, symbol, frequency=Frequency.NONE, n=1, equityType='F', scheduleGroup=None):

        # this function will need a new home.  r will be a response to the quote request, and
        # if successful will contain a content item for each symbol requested.  We need to
        # return those items so they can be consumed to build database.
        # also if token is expired it is trying to run this stuff on the bad response which doesn't work either
        def f():

            # may need to pull the request apart per thing first?
            r = self.api.quoteWithRefresh(symbol)

            if r is not None and r.status_code == 200:

                result = json.loads(r.content)
                dataFrame = FuturesQuote.toDataFrame2(result)

                # At this point we have a reasonable dataframe but it could contain a duplicate row.  FuturesQuote
                # class is static so as of now it can't hold the model/buffer.  Hold it here unless or until we
                # need a more sophisticated model.
                if not self.dataBuffer.empty:
                    dataResult = self.removeDuplicates(dataFrame)
                else:
                    dataResult = dataFrame
                    self.dataBuffer = dataFrame

                # Return the dataframe but more importantly publish to subscribers
                # it is possible that dataResult has no valid rows here and is empty.  For now we let it 
                # publish anyway.
                self.publishQuote(dataResult)
                return dataResult

            elif r is not None:
                print(r)

        # Expand and flatten if we were passed a list
        if type(symbol) is list:
            for s in symbol:
                self.symbols.append(s)
        else:
            self.symbols.append(symbol)

        # Allows using custom schedule groups within an equity type (ex: different schedule frequency for
        # crude mini then S&P500 emini))
        schedulingGroup = scheduleGroup if scheduleGroup is not None else equityType

        # If no specific frequency specified AND we have a schedule, use that.
        if frequency == Frequency.NONE and self.schedule is not None:

            # can't just call getNextOpenInterval because that doesn't return the value that we need, so need
            # to use that information to return the true rate.
            def getInterval():
                rate = 0
                market = equityType

                r = self.schedule.getNextOpenInterval(datetime.now(), market, self.schedule.schedule())

                # Ideally if the market is closed just wait until it opens.  For now just do a slow pull
                # every 60 seconds.
                # r.rateActive is the rate we should use right now, which may be 0.
                # r.rateScheduled is the timer we should use in the next open interval
                wait = r['timeWait']
                rate = (r['rateCurrent'] / 1000.0) if wait <= 0 else 60
                return rate

            # Support a timer per equity type (stock, future, etc)
            # self.timers[schedulingGroup] = RepeatedFnTimer(getInterval, f)
            self.timers[schedulingGroup] = RepeatedThreadTimer(getInterval, f)

        # Just set a basic interval if that's what was specified.
        else:
            interval = (defaultIntervals[frequency] * n) / 1000
            self.timers[schedulingGroup] = RepeatedThreadTimer(lambda: interval, f)

    # Remove any quote in dataFrame which is not newer than an existing quote for same symbol in the data buffer.  Still
    # need this so we can return just the new quote information in dataframe
    def filterStaleDataFrameRows(self, dataFrame, filterColumn):

        try:
            indexDelta = (self.dataBuffer.index.difference(dataFrame.index))

            # If there is no index difference we can use loc and then we want new rows that are not in the existing databuffer.
            # This gives us the new rows where either syumbol is different or the symbol is the same but the time is more recent.
            # We will return these new rows as dataframe.
            if indexDelta is None or indexDelta.empty:
                dataFrame = dataFrame.loc[
                    (dataFrame['symbol'] != self.dataBuffer['symbol']) | (dataFrame[filterColumn] > self.dataBuffer[filterColumn])]

            # Indices don't match.  Concat into one dataframe then filter out old rows later.
            # todo: fix this up debug/test the concatenated dataframe then see what we get with loc.
            else:
                dataFrame = pd.concat(dataFrame, self.dataBuffer, axis=0).sort_values(filterColumn).drop_duplicates().reset_index(drop=True)
                print('Concatenated Dataframe:')
                print(dataFrame)

                dataFrame = dataFrame.loc[
                    (dataFrame['symbol'] != self.dataBuffer['symbol']) | (dataFrame[filterColumn] > self.dataBuffer[filterColumn])]

            return dataFrame

        except Exception as e:
            return None

    @staticmethod
    def printDataFrame(heading, dataframe):
        print(f"{heading} :")
        print(dataframe)
        print()

    # //*** why isn't this in a separate object?
    # Buffer the latest or even last few quotes for each symbol.  Then as new data arrives compare it to the times in the buffer
    # to keep latest and prevent duplicates.  We don't want to return the buffer, we want to return anything new in the dataframe.
    # First get rid of anything in the incoming dataframe that is older than the latest entry for the same symbol in the buffer.
    # Then ideally merge the dataframe and buffer keeping only the most recent entry for each symbol.
    def removeDuplicates(self, dataFrame, useTransactions=True):

        try:
            # 1. if there is no databuffer, there are no duplicates.  Just return the data frame.
            if self.dataBuffer is None:
                print("Null buffer.  Returning received dataframe.")
                self.dataBuffer = dataFrame
                return dataFrame

            if dataFrame is None or dataFrame.empty:
                return

            # Filter based on quote or transactions as desired.
            MarketLink.printDataFrame('Received DataFrame', dataFrame)
            filterColumn = 'timeTrade' if useTransactions is True else 'timeQuote'

            # 1. concatenate dataBuffer with dataFrame.
            self.dataBuffer = pd.concat((self.dataBuffer, dataFrame), axis=0)
            self.dataBuffer = self.dataBuffer.sort_values(by=['symbol', filterColumn]).drop_duplicates(['symbol'], keep='last').reset_index(drop=True)
            MarketLink.printDataFrame('Result Buffer', self.dataBuffer)

            # The above is good and result frame will now have the latest quotes, including whatever the latest in dataframe was.
            # we need to publish only new data, so how do we know what is new data?  Basically delete anything from dataframe that
            # is not in dataBuffer? Because if dataBuffer had a more recent entry, we'd be using the databuffer entry not the
            # dataframe entry.

            # todo: this will probably crash if the indices don't match.  need updated filtering here.
            # todo: the cache could have data that dataFrame doesn't, which we want to ignore.
            # todo: it's also possible that this only happened due to a bug, and shouldn't in real life.
            # todo: but it would be better to be resilient and able to handle this case.
            dataFrame = dataFrame.loc[~((dataFrame['symbol'] == self.dataBuffer['symbol']) & (self.dataBuffer[filterColumn] > dataFrame[filterColumn]))]
            MarketLink.printDataFrame('Returning DataFrame', dataFrame)

            return dataFrame

        except Exception as e:
            return None

    # //*** why isn't this in a separate object?
    # Buffer the latest or even last few quotes for each symbol.  Then as new data arrives compare it to the times in the buffer
    # to keep latest and prevent duplicates.  We don't want to return the buffer, we want to return anything new in the dataframe.
    # First get rid of anything in the incoming dataframe that is older than the latest entry for the same symbol in the buffer.
    # Then ideally merge the dataframe and buffer keeping only the most recent entry for each symbol.
    def removeDuplicates(self, dataFrame, useTransactions=True):

        try:
            # 1. if there is no databuffer, there are no duplicates.  Just return the data frame.
            if self.dataBuffer is None:
                print("Null buffer.  Returning received dataframe.")
                self.dataBuffer = dataFrame
                return dataFrame

            if dataFrame is None or dataFrame.empty:
                return

            # Filter based on quote or transactions as desired.
            MarketLink.printDataFrame('Received DataFrame', dataFrame)
            filterColumn = 'timeTrade' if useTransactions is True else 'timeQuote'

            dataResult = filterStaleQuoteRows(dataFrame, filterColumn)
            MarketLink.printDataFrame('Returning DataFrame', dataResult)

            # 2. concatenate dataBuffer with dataFrame.
            self.dataBuffer = pd.concat((self.dataBuffer, dataFrame), axis=0)
            self.dataBuffer = self.dataBuffer.sort_values(by=['symbol', filterColumn]).drop_duplicates(['symbol'], keep='last').reset_index(drop=True)
            MarketLink.printDataFrame('Result Buffer', self.dataBuffer)

            return dataResult

        except Exception as e:
            return None

    # "Initialize" all configured symbols to ensure we have their data in symbol and metasymbol
    # tables.  Symbols added after main start (if supported at all) should initialize separately.
    def initSymbols(self):
        r = self.api.quoteWithRefresh(self.symbols)

        if r is not None:
            result = json.loads(r.content)
            dataFrame = FuturesQuote.toMetaDataFrame(result)
            self.symbolsInit = True
            self.publishMetaQuote(dataFrame)
            return dataFrame

        else:
            return None

    # Set tick polling speed to tick (in milliseconds)
    def setTickInterval(self, tick):
        self.tickSpeed = max(tick, 100)

    # For each timer, if it has been set, start or stop it.
    def start(self):
        self.isRunning = True

        if not self.symbolsInit:
            self.initSymbols()

        for timer in self.timers:
            self.timers[timer].start()

    def stop(self):
        for timer in self.timers.values():
            timer.stop()
        self.isRunning = False

    # it appears that we are publ;shing meta quote to regular subscriberrs and zmq quote
    # to zmq subscribers.

    # Always publish metadata to direct subscribers.  Publish over ZMQ if enabled.
    def publishMetaQuote(self, dataFrame):
        try:
            if dataFrame is not None and not dataFrame.empty:
                for r in self.subscribers:
                    r.onMetaQuoteAvailable(dataFrame)

                # if self.useZmq:
                #    self.zmqSender.sendTopicPacketPyobj(lambda pomegrani: i+1)

        except Exception as e:
            print(f"Caught exception attempting to publish meta quote: ({type(e)}), {e}")

    # Always publish quote to any direct subscribers.  Publish over ZMQ if enabled.
    def publishQuote(self, dataFrame):
        try:
            if dataFrame is None or dataFrame.empty:
                return

            for r in self.subscribers:
                pass
            #  r.onQuoteAvailable(dataFrame)
            # //*** fix this

            if self.useZmq:
                self.zmqSender.sendDataFrame(dataFrame)

        except Exception as e:
            print(f"Caught exception attempting to publish quote: ({type(e)}), {e}")


def main():
    load_dotenv()

    # Create a MarketLink object, use it to retrieve quote on interval.  Get this having at least the funtionality
    # of the early version but with better organization, then move the real control logic to main.
    marketLink = MarketLink()
    marketLink.addQuoteOnInterval(['/ES', '/GC'], Frequency.SECOND, 2)
    marketLink.addQuoteOnInterval(['/CL', '/MCL'], Frequency.SECOND, 5, scheduleGroup='crude')
    marketLink.start()

    # Mpte that the above no longer prints anything so you will see nothing unless a receiver is connected.


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
