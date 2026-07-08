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
    def addSymbol(self, symbol, frequency=Frequency.MINUTE, n=1):
        if not self.symbols.hasKey(symbol):
            self.symbols[symbol] = {'frequency': (defaultIntervals[frequency] * n) / 1000}
            pass

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
        def f():
            # also if token is expired it is trying to run this stuff on the bad response which doesn't work either

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

    # Use a comparison buffer to remove duplicates from the dataframe.  Do this by combining (concat)
    # the dataframes then dropping duplicates as detected by trade time and active symbol. Keep no 
    # duplicates in the combined frame, and do an inplace replacement.
    #
    # Note: if market is closed this will result in an empty dataframe after removing duplicates (ok)
    # Ideally move this inside futuresQuote object.
    #
    # Remove duplicates (keep none) and return as dataResult.  This is the data that we want to publish.
    # Remove duplicates (keep last) and return (in place) in dataFrame.  This is the updated comparison buffer.
    #
    # Allow use of quote time or transaction time to determine duplicate status.  If not even quote has changed, we definitely do
    # not want the row.  If we want rows with new transactions (not just quote update due to bid/ask size change), then filter
    # by timeTX not timeQuote.  Select this with the useTransactions param (default: true)
    def removeDuplicates0(self, dataFrame, useTransactions=True):

        try:
            # 1. if there is no databuffer, there are no duplicates.  Just return the data frame.
            if self.dataBuffer is None:
                print("Null buffer.  Returning received dataframe.")
                self.dataBuffer = dataFrame
                return dataFrame

            # for each row in the dataframe, if there is a corresponding row in the cache, keep only the one with the latest timeQuote.
            # if there is no corresponding row, add the whole row to the cache
            # ref: https://pandas.pydata.org/pandas-docs/stable/user_guide/10min.html

            print("Existing Buffer: ")
            print(self.dataBuffer)
            print()
            print("Received Frame: ")
            print(dataFrame)
            print()

            # Filter based on quote or transactions as desired.
            filterColumn = 'timeTrade' if useTransactions is True else 'timeQuote'

            # This causes exception when incoming data has different symbols or number of symbols than existing dataBuffer.
            # Is there a way to make loc work here?

            # algo update, since pandas wouldn't help much?
            # we want anything in the dataframe unless it is an older version of something in the cache.

            # todo: index issue with different symbols and/or different numbers of rows.
            # todo: this is probably necessary, but not sufficient.  Even if these both contain quotes for the same instrument,
            # todo: if they are not the same size, loc will still fail.  This is going to require a bit more logic than
            # todo: just using loc

            indexDelta = (self.dataBuffer.index.difference(dataFrame.index))
            if (indexDelta.empty):
                dataFrame = dataFrame.loc[
                    (dataFrame['symbolActive'] != self.dataBuffer['symbolActive']) | (dataFrame[filterColumn] > self.dataBuffer[filterColumn])]

            # if indexes are different we need something fancier
            # placeholder code only for now.  this basically never happens
            else:
                dataFrame = dataFrame.loc[
                    (dataFrame['symbolActive'] != self.dataBuffer['symbolActive']) | (dataFrame[filterColumn] > self.dataBuffer[filterColumn])]

            # If there are NO latest quotes (dataFrame is empty) there is nothing else to do.
            if not dataFrame.empty:

                dropList = list()

                # At the point dataFrame contains only the new/updated quotes and is non-empty.  Now need to merge the latest quotes into the
                # buffer, while returning any latest quotes.  Pandas makes this normally simple operation extremely difficult.  Most of its
                # methods have subtleties the render them useless for this type of operation.  For that reason, we iterate the dataframe of
                # updated quotes, find any rows with matching symbols in dataBuffer, add {key} for row to list, and then drop all rows
                # in the list.  Ridiculous, but whatever.

                # Note that we want to find by symbol not symbolActive here as when a contract roll happens the symbolActive will change but
                # the symbol won't.  This happened 2021-10-16 with crude oil and caused error.
                for row in dataFrame.itertuples():
                    x = self.dataBuffer.loc[self.dataBuffer['symbol'] == row.symbol]
                    if x.index is not None and not x.index.empty:
                        dropList.append(x.index[0])

                if dropList:
                    self.dataBuffer.drop(dropList, inplace=True)

                # Now databuffer has a spot made for the updated row versions in dataFrame.  Append them, make any needed index fix, and
                # hopefully go on...
                self.dataBuffer = pd.concat((self.dataBuffer, dataFrame), axis=0)
                self.dataBuffer.reindex_like(dataFrame)

                # NOTE: Kept as an example of pandas drop()
                # This data is all newer than anything equivalent in the dataBuffer.  So
                #  we want to drop any such rows in the buffer.
                # Probably there is a way to do this with merge (there should be).
                # cacheLines = self.dataBuffer[(dataFrame['symbolActive'] == self.dataBuffer['symbolActive']) & (dataFrame['timeQuote'] > self.dataBuffer['timeQuote'])]
                # self.dataBuffer.drop(cacheLines.index, inplace=True)

                return dataFrame

        except Exception as e:
            return None

    # Remove any quote in dataFrame which is not newer than an existing quote for same symbol in the data buffer.
    def filterStaleDataFrameRows(self, dataFrame, filterColumn):

        try:
            indexDelta = (self.dataBuffer.index.difference(dataFrame.index))
            if indexDelta.empty:
                dataFrame = dataFrame.loc[
                    (dataFrame['symbolActive'] != self.dataBuffer['symbolActive']) | (dataFrame[filterColumn] > self.dataBuffer[filterColumn])]

            # If indexes don't match loc() won't work.  But it also won't work if any columns are different, and will throw exception.
            # todo: handle all index conditions.
            else:
                dataFrame = dataFrame.loc[
                    (dataFrame['symbolActive'] != self.dataBuffer['symbolActive']) | (dataFrame[filterColumn] > self.dataBuffer[filterColumn])]

            return dataFrame

        except Exception as e:
            return None


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

            # for each row in the dataframe, if there is a corresponding row in the cache, keep only the one with the latest timeQuote.
            # if there is no corresponding row, add the whole row to the cache
            # ref: https://pandas.pydata.org/pandas-docs/stable/user_guide/10min.html

            print("Existing Buffer: ")
            print(self.dataBuffer)
            print()
            print("Received Frame: ")
            print(dataFrame)
            print()

            # Filter based on quote or transactions as desired.
            filterColumn = 'timeTrade' if useTransactions is True else 'timeQuote'
            dataFrame = self.filterStaleDataFrameRows(dataFrame, filterColumn)

            if dataFrame.empty:
                return

            # now we want to select a dataframe where we take the latest quote for each unique
            # symbol from dataframe and databuffer and combine them
            # daA.where(activeSymbol not in dfB or time > dfB.time where adtiveSymbol == dfB.activeSymbol)
            # pretty much same for dfB


            # now dataframe should contain latest quotes.  we want to select the combination of
            dropList = list()

            # At the point dataFrame contains only the new/updated quotes and is non-empty.  Now need to merge the latest quotes into the
            # buffer, while returning any latest quotes.  Pandas makes this normally simple operation extremely difficult.  Most of its
            # methods have subtleties the render them useless for this type of operation.  For that reason, we iterate the dataframe of
            # updated quotes, find any rows with matching symbols in dataBuffer, add {key} for row to list, and then drop all rows
            # in the list.  Ridiculous, but whatever.

            # Note that we want to find by symbol not symbolActive here as when a contract roll happens the symbolActive will change but
            # the symbol won't.  This happened 2021-10-16 with crude oil and caused error.
            for row in dataFrame.itertuples():
                x = self.dataBuffer.loc[self.dataBuffer['symbol'] == row.symbol]
                if x.index is not None and not x.index.empty:
                    dropList.append(x.index[0])

            if dropList:
                self.dataBuffer.drop(dropList, inplace=True)

            # Now databuffer has a spot made for the updated row versions in dataFrame.  Append them, make any needed index fix, and
            # hopefully go on...
            self.dataBuffer = pd.concat((self.dataBuffer, dataFrame), axis=0)
            self.dataBuffer.reindex_like(dataFrame)

            return dataFrame

            return dataFrame


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
            if dataFrame.empty:
                print("empty dataframe")
                return

            if dataFrame is not None and not dataFrame.empty:
                for r in self.subscribers:
                    pass
                #  r.onQuoteAvailable(dataFrame)
                # //*** fix this

                if self.useZmq:
                    self.zmqSender.sendDataFrame(dataFrame)

        except Exception as e:
            print(f"Caught exception attempting to publish meta quote: ({type(e)}), {e}")


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
