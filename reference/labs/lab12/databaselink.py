
from dotenv import load_dotenv
from enum import IntEnum
from pandas import DataFrame

import psycopg2
import psycopg2.extensions
import os
import sys

# https://medium.com/analytics-vidhya/pandas-dataframe-to-postgresql-using-python-part-1-93f928f6fac7
from psycopg2 import OperationalError, errorcodes, errors


class DBError(IntEnum):
    NONE = 0,
    SUCCESS = 1,
    UNKNOWN = -1,
    MISSING_SYMBOL = 2,
    MISSING_FK = -4,
    DUPLICATE = 8


# This is mostly MarketDatabaseLink unless the general purpose parts of it become useful enough to exist in their own right.
class DatabaseLink:
    def __init__(self, db):
        self.psqlUser = None
        self.psqlPassword = None
        self.psqlHost = None
        self.psqlPort = None
        self.psqlDb = db
        self.tdCode = None
        self.tdCode2 = None
        self.envInit = False
        self.connected = False
        self.connection = None

    # database is now set when creating
    def initEnvironment(self, db):
        self.psqlUser = os.getenv("PSQL_USER")
        self.psqlPassword = os.getenv("PSQL_PASSWORD")
        self.psqlHost = os.getenv("PSQL_HOST")
        self.psqlPort = os.getenv("PSQL_PORT")
        self.tdCode = os.getenv("TD_CODE")
        self.tdCode2 = os.getenv("TD_CODE2")
        self.envInit = True
        # psqlDb = os.getenv("PSQL_DB")

    # Connect using the configured environment data
    # todo: update to return connection to support fluent style
    def connect(self):
        if not self.envInit:
            self.initEnvironment(self.psqlDb)

        if self.connected:
            self.disconnect()

        result = False
        cursor = None

        try:
            # Sadly there is no easy way to monitor connection state with psycopg2
            self.connection = psycopg2.connect(user=self.psqlUser,
                                               password=self.psqlPassword,
                                               host=self.psqlHost,
                                               port=self.psqlPort,
                                               database=self.psqlDb)

            cursor = self.connection.cursor()
            cursor.execute("SELECT version();")
            record = cursor.fetchone()
            print("You are connected to: ", record, "\n")
            result = True

        except (Exception, psycopg2.Error) as error:
            print(f"Error connecting to database: {error}")
            self.connection = None
            result = False

        finally:
            if cursor is not None:
                cursor.close()
            return result

    def disconnect(self):

        try:
            if self.connection is not None:
                self.connection.close
                self.connection = None

        except (Exception, psycopg2.Error) as error:
            print(f"Error disconnecting from database: {error}")

        finally:
            self.connected = False

    # Note that neither the psycopg3 refs below nor the psycopg example are compatible
    # with psycopg2.  For that, we need this:
    # https://access.crunchydata.com/documentation/psycopg2/2.7.4/faq.html
    #
    # https://www.psycopg.org/psycopg3/docs/basic/adapt.html
    # https://www.psycopg.org/psycopg3/docs/advanced/adapt.html#adapt-example-float
    def useFloatAdapter(self, useFloat):
        if self.connection is None:
            return self.connection

        if useFloat:
            DEC2FLOAT = psycopg2.extensions.new_type(
                psycopg2.extensions.DECIMAL.values,
                'DEC2FLOAT',
                lambda value, curs: float(value) if value is not None else None)
            psycopg2.extensions.register_type(DEC2FLOAT)

            # code from old example.  does not work in psycopg2
            # self.connection.adapters.register_loader("numeric", psycopg2.types.numeric.FloatLoader)

        else:
            print("Unregistering adapters is not currently supported.  Create a new connection.")

        return self.connection

    # add tick data: if meta symbol not present, call addMetaSymbol
    # add these method next and start testing them
    def addSymbol(self, symbol, assetType, assetMainType, exchange):

        try:
            cursor = self.connection.cursor()
            cursor.callproc('addsymbol', (symbol, assetType, assetMainType, exchange,))
            result = cursor.fetchone()[0]
            if result == DBError.SUCCESS:
                self.connection.commit()

        except:
            pass

        finally:
            cursor.close()
            return result

    # This isn't called particularly often so don't worry about insert/execute many here.
    # datetimes here are expected to be unix times *with* milliseconds
    def addMetaSymbol(self,
                      symbol, symbolActive, description,
                      dateActive, dateExpire, dateRollover,
                      tick, tickAmount, futureMultiplier,
                      delayed, realTime, isActive
                      ):

        try:
            # ref: https://stackoverflow.com/questions/28409134/string-passed-into-cursor-callproc-becomes-unknown-psycopg2-python-2-7-postgr
            cursor = self.connection.cursor()
            cursor.callproc('addmetasymbol', (
                symbol, symbolActive, description,
                dateActive, dateExpire, dateRollover,
                tick, tickAmount, futureMultiplier,
                delayed, realTime, isActive))

            result = cursor.fetchone()[0]

            if result == 1:
                self.connection.commit()

        # we can't just do an insert/executemany here
        # https://medium.com/analytics-vidhya/part-3-1-pandas-dataframe-to-postgresql-using-python-8a3e3da87ff1
        # but if we had a function that had these parameters it would work.    
        finally:
            cursor.close()

        return result

    # For this we want to add all of the tick data contained in the dataframe, and just use one commit.  
    # 
    # timeTx, timeQuote
    # symbol, symbolActive
    # OHLC
    # mark, last, bid, ask
    # lastSize, bidSize, askSize
    # volume, open interest
    def addTickData(self, dataFrame):

        try:
            # tuples = [tuple(x) for x in dataFrame.to_numpy()]
            # print(tuples)

            # v0: just call in loop, verify that we can create objects.
            # v1: use execute_many or similar to speed up
            cursor = self.connection.cursor()
            for row in dataFrame.itertuples():
                cursor.callproc('addTickData', (
                    row.timeTrade,
                    row.timeQuote,
                    row.symbol,
                    row.symbolActive,
                    row.O,
                    row.H,
                    row.L,
                    row.C,
                    row.mark,
                    row.last,
                    row.bid,
                    row.ask,
                    row.lastSize,
                    row.bidSize,
                    row.askSize,
                    row.volumeTotal,
                    row.openInterest
                ))

            result = cursor.fetchone()[0]

            if result == DBError.SUCCESS:
                self.connection.commit()

            else:
                print(f"tick data error: {result}")

        finally:
            cursor.close()

    def cursor(self):
        return self.connection.cursor()

    # This should probably use a named cursor?
    def getTickData(self, symbol, timeStart, timeEnd, volumeOnly=False, chunkSize=-1):

        try:
            cursor = self.connection.cursor()
            cursor.callproc('getTickData', (
                symbol,
                timeStart,
                timeEnd,
                volumeOnly,
                chunkSize
            ))
            return DataFrame(cursor.fetchall())

        finally:
            cursor.close()

    def getTickDataLast(self, symbol, n):
        try:
            cursor = self.connection.cursor()
            cursor.callproc('getTickDataLast', (symbol, n))
            return DataFrame(cursor.fetchall())

        finally:
            cursor.close()

    @staticmethod
    def urlDecode(code):
        decode = unquote(code)
        return decode

    # error handling
    # https://medium.com/analytics-vidhya/pandas-dataframe-to-postgresql-using-python-part-1-93f928f6fac7
    def showError(err):
        # get details about the exception
        err_type, err_obj, traceback = sys.exc_info()

        # get the line number when exception occured
        line_n = traceback.tb_lineno

        # print the connect() error
        print("\npsycopg2 ERROR:", err, "on line number:", line_n)
        print("psycopg2 traceback:", traceback, "-- type:", err_type)

        # psycopg2 extensions.Diagnostics object attribute
        print("\nextensions.Diagnostics:", err.diag)

        # print the pgcode and pgerror exceptions
        print("pgerror:", err.pgerror)
        print("pgcode:", err.pgcode, "\n")  #


# Simple test if this file is run directly.  Ensure we can connect, and handle error if DB is not available.
# Ideally perform buffering and send alarms if DB is not available.
# Question: how difficult would it be to adapt neuron to this compared to creating another app of similar structure?
def main():
    # figure out timezone -- change postgres config to UTC?
    def testAddMetaSymbol():
        # result = addMetaSymbol(cursor)         
        # result = dbLink.addMetaSymbol(
        #    '/ES', '/ESZ21', 'S&P 500 e-mini Dec 2021',
        #   '2021-09-21 00:00:00', '2021-12-28 23:59:59', '2021-12-21 23:59:59',
        #    0.25, 12.50, 50.0,
        #    False, True, True
        # )
        # print(f"addmetasymbol returned: {result}")

        # Missing symbol.  Add it now.
        # if result == -4:
        #    result = dbLink.addSymbol('/ES', 'F', 'F', 'CME',)
        #    if result == 1:
        #        print("Committing transaction")
        #        result = dbLink.addMetaSymbol(
        #            '/ES', 'ESZ21', 'S&P 500 e-mini Dec 2021',
        #            '2021-09-21 00:00:00', '2021-12-28 23:59:59', '2021-12-21 23:59:59',
        #            0.25, 12.50, 50.0,
        #            False, True, True
        #        )

        #    else:
        #        print(f"Result: ", result)

        # if metasymbol add fails for FK constraint, it means the symbol isn't present.  Add
        # that first, then retry.
        # symbol (char 6), assettype (char), assetmaintype (char), exchange (char 8)
        # ex: '/ES', 'F', 'F', 'CME'
        # elif result == -1:
        #    print("Unknown error (code: -1) when adding metasymbol")

        # in order to handle adding quote data in a robust manner, we should be able to
        # handle adding the metaSymbol and if necessary, the symbol.
        # we have metadata from a quote data.
        pass

    # Test storing some actual objects to the database.
    def testDBLink():
        try:
            dbLink = DatabaseLink('marketdata')
            if dbLink.connect():
                # 1) call a very basic postgres user-defined function and get its result
                cursor = dbLink.cursor()
                cursor.callproc('testFunction', (3,))
                result = cursor.fetchone()
                print(f"Function returned: ", {result})
                cursor.close()

                n = 50
                result = dbLink.getTickDataLast('/ES', n)
                print(f'get tick data last {n}:')
                print(result)

                result = dbLink.getTickData('/ES', '2021-10-13:00:00:00', '2021-10-14:23:59:00')
                print('get tick data:')
                print(result)

        finally:
            dbLink.disconnect()

    load_dotenv()
    testDBLink()


# Call main function *if* this is the main module.  This provides a familiar structure
# often used with many other languages.
if __name__ == '__main__':
    main()

else:
    print(__name__)
