from os import stat
import pandas as pd
import numpy as np
from dataclasses import dataclass, field


# Original documentation said this object handles sorting json result into a pandas dataframe.  This
# may not have ever been accurate.

class FuturesQuote:

    def __init__(self):
        self.epsilon = 0.00001
        pass

    # Only CME is supported for now.
    @staticmethod
    def translateExchange(_exchange):
        exchange = _exchange.lower()
        return 'CME' if exchange == 'e' else None

    # Only futures are supported for now.
    @staticmethod
    def translateAssetType(_assetType):
        assetType = _assetType.lower()
        return 'F' if assetType == 'future' else None

    # try to convert json result set into dataframe
    @staticmethod
    def toDataFrame2(result):

        dataFrame = pd.DataFrame()

        for i in result.items():

            # First just scrub the dict values instead of creating a dataframe of one item here and operating
            # on that.
            tempFrame = FuturesQuote.scrubQuoteDict(i[1])
            if tempFrame is None:
                continue

            # If we don't care about column names we can save a lot of time.
            dataAppend = pd.DataFrame(tempFrame, columns=[
                'quoteTimeInLong',
                'tradeTimeInLong',
                'symbol',
                'futureActiveSymbol',
                'bidPriceInDouble',
                'askPriceInDouble',
                'openPriceInDouble',
                'highPriceInDouble',
                'lowPriceInDouble',
                'closePriceInDouble',
                'lastPriceInDouble',
                'mark',
                'bidSizeInLong',
                'askSizeInLong',
                'lastSizeInLong',
                'totalVolume',
                'openInterest'], index=[0])

            dataAppend = FuturesQuote.mapDataFrame(dataAppend)
            dataFrame = pd.concat((dataFrame, dataAppend), axis=0)

        return dataFrame

    # deprecated method: Accept a json response and return a dataframe.
    # Map proprietary dataframe fields to something consistent.  If this gets too slow just rename the column
    # names directlyh, but be careful to include all of them.
    # deprecated
    @staticmethod
    def mapDataFrameRename(dataFrame):
        dataFrame.rename(columns={
            'quoteTimeInLong': 'timeQuote',
            'tradeTimeInLong': 'timeTrade',
            'futureActiveSymbol': 'symbolActive',
            'bidPriceInDouble': 'bid',
            'askPriceInDouble': 'ask',
            'openPriceInDouble': 'O',
            'highPriceInDouble': 'H',
            'lowPriceInDouble': 'L',
            'closePriceInDouble': 'C',
            'lastPriceInDouble': 'last',
            'bidSizeInLong': 'bidSize',
            'askSizeInLong': 'askSize',
            'lastSizeInLong': 'lastSize',
            'totalVolume': 'volumeTotal',
        }, inplace=True)

        return dataFrame

    @staticmethod
    # Either map this way and specify ALL columns or use the rename method and specify only changed columns.
    def mapDataFrame(dataFrame):
        dataFrame.columns = [
            'timeQuote',
            'timeTrade',
            'symbol',
            'symbolActive',
            'bid',
            'ask',
            'O',
            'H',
            'L',
            'C',
            'last',
            'mark',
            'bidSize',
            'askSize',
            'lastSize',
            'volumeTotal',
            'openInterest'
        ]

        return dataFrame

    @staticmethod
    def mapMetaDataFrame(dataFrame):
        dataFrame.rename(columns={
            'futureActiveSymbol': 'symbolActive',
            'futureExpirationDate': 'dateExpire',
            'realtimeEntitled': 'realTime',
            'futureIsActive': 'isActive'
        }, inplace=True)
        return dataFrame

    # Reject for missing critical data.
    @staticmethod
    def scrubQuoteDict(row):
        epsilon = 0.00001

        # Reject for missing bid and ask price as it would be hard to work with that.
        if row['bidPriceInDouble'] < epsilon or row['askPriceInDouble'] < epsilon:
            return None

        # Fix close and last as needed
        if row['closePriceInDouble'] < epsilon < row['lastPriceInDouble']:
            row['closePriceInDouble'] = row['lastPriceInDouble']
        elif row['closePriceInDouble'] > epsilon > row['lastPriceInDouble']:
            row['lastPriceInDouble'] = row['closePriceInDouble']

        # Put all the relevant pieces into an iterable
        tempData = [row['openPriceInDouble'], row['highPriceInDouble'], row['lowPriceInDouble'], row['closePriceInDouble'], row['lastPriceInDouble'],
                    row['mark'], row['bidPriceInDouble'], row['askPriceInDouble']]

        if row['lowPriceInDouble'] < epsilon:
            low = row['lowPriceInDouble']

            minPositive = min(i for i in tempData if i > epsilon)
            row['lowPriceInDouble'] = minPositive

            # print(F"Fixing low {low}, new value: {minPositive}")

        if row['highPriceInDouble'] < epsilon:
            row['highPriceInDouble'] = max(tempData)

        # What about opening price?  Can try setting it to the close but this is probably going to just be a mess
        if row['openPriceInDouble'] < epsilon:
            row['openPriceInDouble'] = row['closePriceInDouble']

        # Hopefully this doesn't make a copy...
        return row

    # Process dataframe and attempt to "fix" any missing values.  Return dataframe and status of processing.
    # This function assumes a "1 row dataframe" which would seem pointless except that it's a really easy
    # way to convert JSON received from the TD API.
    @staticmethod
    def scrubQuoteData(dataFrame):

        epsilon = 0.00001

        # 2022 Update: old comment expresses frustration.  This is a good thing to look into.

        # find all rows that we would want to modify?  it is crazy that this normally simple thing is so 
        # difficult.  it might be easier to just manually deal with the json and pull the values out if needed
        #
        # recommended to use .array or .to_numpy here.  really this is mostly nonsense as this is *always* a
        # dataframe of one thing  should be able to create a numpy array from it.

        # what an annoying thing to be stuck on.  right here we just need to read ONE quote from json and create
        # a dataframe from it

        def scrubRow(row):
            # Reject for missing bid and ask price as it would be hard to work with that.
            if row['bid'] < epsilon or row['ask'] < epsilon:
                pass

            # Fix close and last as needed
            if row['C'] < epsilon < row['last']:
                row['C'] = row['last']
            elif row['C'] > epsilon > row['last']:
                row['last'] = row['C']

            # Put all of the relevant pieces into an iterable
            tempData = [row['O'], row['H'], row['L'], row['C'], row['last'], row['mark'], row['bid'], row['ask']]

            if row['L'] < epsilon:
                low = row['L']

                minPositive = min(i for i in tempData if i > epsilon)
                row['L'] = minPositive

                print(F"Fixing low {low}, new value: {minPositive}")

            if row['H'] < epsilon:
                row['H'] = max(tempData)

            # What about opening price?  Can try setting it to the close but this is probably going to just be a mess
            if row['O'] < epsilon:
                row['O'] = row['C']

        # This should work but might be much slower than iterrows + at (or itertuples)
        dataFrame.apply(scrubRow, axis=1)
        return dataFrame

    # Ideally read this first before pulling higher frequency data.  Ensure that symbol and meta symbol
    # is created for any symbols we care about.  
    # response is assumed to be already translated into json with json.loads(response.content) first
    @staticmethod
    def toMetaDataFrame(jsonResult):

        epsilon = 0.00001
        dataFrame = pd.DataFrame()

        # TODO: add asset type, exchange (then: { symbol, assetType, assetMainType, exchange })
        #       add code to translate asset type and exchange from TD to MarketLink vocabulary
        #       there may be a better way to do this than calling append in a loop

        for i in jsonResult.items():
            dataAppend = pd.DataFrame(i[1], columns=[
                'symbol',
                'futureActiveSymbol',
                'assetType',
                'assetMainType',
                'exchange',
                'description',
                'futureExpirationDate',
                'tick',
                'tickAmount',
                'futureMultiplier',
                'delayed',
                'realtimeEntitled',
                'futureIsActive'
            ], index=[0])

            dataAppend = FuturesQuote.mapMetaDataFrame(dataAppend)

            # FIXME: These are not correct just placeholders while adding the row.
            dataAppend['dateActive'] = dataAppend['dateExpire']
            dataAppend['dateRollover'] = dataAppend['dateExpire']

            ## If we can pass some basic validity, append to the real dataframe
            if not dataAppend['tick'].empty and dataAppend['tick'][0] > epsilon:
                dataAppend['assetType'] = dataAppend['assetType'].apply(FuturesQuote.translateAssetType)
                dataAppend['assetMainType'] = dataAppend['assetMainType'].apply(FuturesQuote.translateAssetType)
                dataAppend['exchange'] = dataAppend['exchange'].apply(FuturesQuote.translateExchange)
                dataFrame = pd.concat((dataFrame, dataAppend), axis=0)

            else:
                print("Discarding:")

        return dataFrame


# 2022: why is this here?  No usages found.
def toDataFrame(result):
    epsilon = 0.00001
    dataFrame = pd.DataFrame()

    for i in result.items():

        dataAppend = pd.DataFrame(i[1], columns=[
            'quoteTimeInLong',
            'tradeTimeInLong',
            'symbol',
            'futureActiveSymbol',
            'bidPriceInDouble',
            'askPriceInDouble',
            'openPriceInDouble',
            'highPriceInDouble',
            'lowPriceInDouble',
            'closePriceInDouble',
            'lastPriceInDouble',
            'mark',
            'bidSizeInLong',
            'askSizeInLong',
            'lastSizeInLong',
            'totalVolume',
            'openInterest',
            'changeInDouble'
        ], index=[0])

        dataAppend = FuturesQuote.mapDataFrameRename(dataAppend)
        print('Column remap: ')
        print(dataAppend)

        dataAppend = FuturesQuote.scrubQuoteData(dataAppend)

        if not dataAppend.empty:
            dataFrame = pd.concat((dataFrame, dataAppend), axis=0)
            print(dataAppend)

        else:
            print("Discarding useless dataframe:")
            print(dataFrame)

        # print(dataAppend.to_string(index=False, header=False))

    return dataFrame
