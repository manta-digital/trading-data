import csv
import pandas as pd
from backtesting.test import GOOG
from backtesting import Backtest
from backtesting import Strategy
from backtesting.lib import crossover
from dotenv import load_dotenv

from databaselink import DatabaseLink


def SMA(values, n):
    """
    Return simple moving average of `values`, at
    each step taking into account `n` previous values.
    """
    return pd.Series(values).rolling(n).mean()

# we could either add in othere stuff or use this class inside of something bigger.
# this just needs init and next.  sometimes in next we want to actually do something.
# we could potentially raise some type of signal here too?



# Class from quickstart: https://kernc.github.io/backtesting.py/doc/examples/Quick%20Start%20User%20Guide.html
# Note that this is basically a terrible strategy, so it will be interesting to see how it performs
class SmaCross(Strategy):
    # Define the two MA lags as *class variables*
    # for later optimization
    n1 = 15
    n2 = 100
    enableLong = True
    enableShort = True

    stopPoints = 1.00
    targetPoints = 3.00
    orderSize = 1

    def init(self):
        # Precompute the two moving averages
        self.sma1 = self.I(SMA, self.data.Close, self.n1)
        self.sma2 = self.I(SMA, self.data.Close, self.n2)

    def next(self):

        try:
            price = self.data.Close[-1]

            # If sma1 crosses above sma2, close any existing
            # short trades, and buy the asset
            if crossover(self.sma1, self.sma2):
                # self.position.close()
                if self.enableLong and not self.position:
                    self.buy(size=self.orderSize, limit=price + 0.5, sl=price - self.stopPoints, tp=price + self.targetPoints)

            # Else, if sma1 crosses below sma2, close any existing
            # long trades, and sell the asset
            elif crossover(self.sma2, self.sma1):
                # self.position.close()

                if self.enableShort and not self.position:
                    self.sell(size=self.orderSize, limit=price - 0.5, sl=price + self.stopPoints, tp=price - self.targetPoints)

        except Exception as e:
            pass


# Fairly terrible name but we need an easy way to get historical data from the Postgres DB for
# a symbol and a time range.  Given: symbol, time start, time end.  Return data (whole frame possibly
# but at least OHLCV)
#
# 1. Get the connection/db accessobject
# 2. Run query
# 3. Mash intp pandas dataframe
#
# tl;dr: pulls tick data from database.  no metadata (column names/info) are available yet.  Filters
# returned dataframe, renames the filtered columns to keep backtesting.py happy, sets index to the
# time column.  currently prints data though it shouldn't.
#
# unfortunately this doesn't work as well with futures data which is not OHLC.
def getTickDataTD():
    # we need whatever environment marketlink is using
    load_dotenv()

    # need an event or something?  to know when connection is made?  this should be async.
    dbName = 'marketdata'
    dbLink = DatabaseLink(dbName)
    status = dbLink.connect()

    if not status:
        print("Error connecting to database ", dbName)
        return None

    # Register adapter to transform the decimal data to float.
    dbLink.useFloatAdapter(True)

    # todo: update to fetch metadata schema so we know what columns are what.  ideally return it
    # todo: in a dataframe with the correct column labels in the first place
    # using:
    #   last: close

    quoteData = dbLink.getTickData('/ES', '2021-10-04 03:49:55.831000', '2021-10-05 05:13:48.545000')
    quoteData = quoteData[[0, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15]]
    quoteData = quoteData.rename(columns={
        0: 'Time',
        4: 'Open-temp',
        5: 'High',
        6: 'Low',
        9: 'Close',
        10: 'bid',
        11: 'Open',  # ask
        12: 'lastsize',
        13: 'bidsize',
        14: 'asksize',
        15: 'Volume'
    })

    quoteData.set_index('Time', inplace=True)
    quoteData.astype({
        'Open': float,
        'High': float,
        'Low': float,
        'Close': float,
        'Volume': float,
        'bid': float,
        # 'ask': float
    })
    return quoteData


def getHistoricalDataBarchart(filePath):
    load_dotenv()
    quoteData = pd.read_csv(filePath)
    quoteData.set_index('timestamp', inplace=True)
    quoteData = quoteData.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    })

    quoteData = quoteData[['Open', 'High', 'Low', 'Close', 'Volume']]

    return quoteData


def main():
    filePath = 'data/ESH20_20200101_20200223.csv'

    data = getTickDataTD()
    data = getHistoricalDataBarchart(filePath)

    with pd.option_context('display.max_rows', 32,
                           'display.max_columns', None,
                           'display.precision', 3):
        print(data)

    try:
        # run the strategy on some sample data.  it prints stats but not the ending
        # cash value, which seems really dumb.
        # margin 0.02 = 50:1 leverage
        bt = Backtest(data, SmaCross, cash=10000, commission=0.002, exclusive_orders=True, margin=0.02)
        stats = bt.run()
        print(stats)
        bt.plot()

    except Exception as ex:
        print(ex)


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
