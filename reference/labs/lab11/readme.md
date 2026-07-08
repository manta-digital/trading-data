
Trading Lab 11
---------------------------------------------------------------------------------------------------
* New Feature: connected backtesting.py to futures data and verified strategies can run.  This has
  also highlighted issues from attempting to use realtime data which does not have regular OHLC 
  periods.  Will need to be able to adapt RT data to historical.
* Timers: replaces Timer based events with Thread based.  Timer derives from thread but Python
  timers create a new thread with each execution.  This causes increased overhead as well as extra
  complexity, and difficulty in debugging.  Thread-based version uses a single timer thread.  Current
  implementation will drift over time.  To be updated in future version.
* Replaced all deprecated Pandas append() calls with concat().
* Once dataframe index exception is fixed this lab is complete.



Trading Lab 10
---------------------------------------------------------------------------------------------------
* Implement basic ZMQ senders and receivers.  
* Make sure to have easy and documented method of running them.
* For receiver, display useful/readable quote information.
* Figure out structure -- is this microservices or bridged?
* Get some tests in place, at the very least some unit tests.
* > above items are acceptance criteria for lab 10.  strategy and backtesting come after.



Current Status
---------------------------------------------------------------------------------------------------
* fixed bug that prevented receiver from connecting if sender wasn't started first.
* zmq sender is sending the entire dataframe.  receiver gets it but doesn't know what to do with it.
* ideally we have a sender and multiple receivers.  we probably don't need any kind of router or 
  coordinator quite yet but we might in the future, especially if we can combine strategies.  For
  now just have a single receiver and display some data.
* starting the program
  * in lab10, python main.py to run the sender
  * in lab10, python zmqreceiver.py to run the receiver


Updates
---------------------------------------------------------------------------------------------------
* removed ZMQ Sender from LinkReceiver.  Marketlink now directly incorporates ZmqSender.
* Creating new ZmqPoller after timeout.  Greatly increased timeout value.

* todo: split into meta and quote.
* todo: process quote packet
* todo: consider message count to make sure none getting lost




Future Goals:
---------------------------------------------------------------------------------------------------
* Start creating strategy objects.  At the very least need to pick a TA library and create objects
  to start incorporating data.  Be able to receive data in realtime, primed with correct amount of
  historical data if needed (to be able to calculate the required moving averages, for example.


* Continue to refine publish / subscribe and allow a subscribing object to receive updates on the
  topic of its choice.  Use the multipart message zmq code.

* Ability to add command line arguments for at least connection options would be nice.

* Figure out how backtesters work.  They must have some kind of strategy language, or do you just
  have to write a function by hand?  Having a strategy language would be VERY useful.  What would
  such a language look like?

  1. Categorize signals
  2. Define strategic events (MA crosses, formation/break of technical patterns)
  3. Recognize technical patterns (recognition is required for strategy beased on the recognition).


* Start figuring out how and when to keep calculated metadata (avg velocity, for example) and what
  time granularity to use for this (n sec?)


Detecting Conditions in Realtime Data
---------------------------------------------------------------------------------------------------
1) Pick a condition to detect (ex: 100MA cross above 200MA)
2) Calculate (realtime) the data needed to detect the conditions (100MA, 200MA)
3) Write a function that returns true when a signal should be generated (later do 0.0-1.0 not true/false)

Issues:
1) how does this work with tick data?  It may need to be aggregated first, and use some type of virtual chart bars?  It seems that most backtesting systems do not work with ticks, because they
   use defined periods (minute, hour, day, etc).  It's easy to create an indicator from these because you know the period to use for the MA. It appears that many of the strategy rollers may have considerable difficulty with tick data.  How
   do we aggregate this?  Because we calculate something like an MA over chart periods where a chart period = n ticks (ex: n = 133).

   So what do we want an MA *of*?  Chart bars?  Chart bar frequency * transactions can that work?  For
   example if we have 133 transactions/bar and we want an average of 100 chart bars, can we use 13300
   transactions MA? Maybe but it's going to take some analysis to see if this works.

   How to aggregate ticks?  We have tick data which is every transaction and even some with no transactions
   where we get a quote update and bid/ask size changes but not actual transactions.  

   1. Get the max expected needed number of transactions (ex: 100MA with 133 transactions/bar, get 13300).
   2. Aggregate them (use pandas?) into 100 virtual OHLC bars.
   3. Feed this into technical analysis library to calculate the MA.
   4. Track when a new bar is formed, and calculate the average of it.

   Later, work on varying the number of transactions and/or adjusting for speed.


Where to start?
---------------------------------------------------------------------------------------------------
1) On receiving data, filter out anything we don't care about. dataFrame = dataFrame['/ES'].  Either
   trim the symbols ('/ES    ') and/or update the procedure to handle that for us.  For now we can
   filter.  Now have a dataframe with just the new data for our symbol.  One issue, it has rows with
   just bid/ask changes, we probably don't want to use these for strategy points without an actual
   transaction update.

   This does raise the question of do we even want these rows with no volume changes?  For tick data,
   we could reduce footprint considerablye with:
   1. Volume changes only.
   2. No OHLC (we aggregate it ourselves and it changes based on the ticks/bar used).

   But is it useful to have quote rows with only bid/ask changes and no transaction changes?  It
   potentially could be for small scalps.  Otherwise it probably isn't.  If it isn't often useful, it
   would be more space and CPU efficient to only store when we get a new transaction.  If doing this
   back up the table and re-create it.  Do an extra glacier backup first.

   1. When we aggregate and do any normal symbols, the no-transaction quotes don't matter.  They might
      for more rarely traded commodities?  They could create the equivalent of a small trend action?

   2. If the price was not moving but bid size and amount were increasing (spread narrowing) this
      could be useful.

   3. Can we accurately find this stuff?  Like accurately filter ticks with volume changes only?  We
      want quotes that only happen one time, maybe...If we have 2 quotes for 1 transaction which one
      do we want?  We want the one when the transaction happens.  This is the first price change?
      If n = 1 (transaction and quote same time, no new quote without transaction), then it's easy.

      If n > 1 (ex: n == 2) now we have 1 transaction (at the first record) and one additional quote
      with no transaction, we want the first one.  So we partion over and take the first record from
      every partition.  

      For now we do two things:
      (ok) 1. Stop recording OHLC for tick data (send 0 as PH for now).  Stop recording rows for which 
	 there is no transacton change.  This means a change to removeDuplicateRows.  Will need to go 
 	 back through old data and puill this stuff out.

      2. Rewrite the tickFilter function to filter out the emini rows without a volume update.  Could
         just select all rows with unique timeTX.  This is easy it's just the filterTickData function
         with n = 1.

   Resolution: will keep only transaction updates not quote updates with no transaction when storing
   tick data.  Also do not need to pull or store OHLC for tick data.


2) Create a new program that incorporates ZMQ receiver so we can receive the dataframes with new 
   quotes and filter down to what we want.

   In order for (1) to work, create a new program, incorporate the ZMQ receiver into it and have it 
   start creating a model to work with.  Need to decide where to aggregate, but aggregating in
   pandas is probably the easiest (hopefully).


3) Prime with historical data and be able to aggregate.

   1. Prime with historical data by specifying an amount to retrieve.  Retrieve this into a dataframe,
      and for now assume it can always fit into memory.  Not a generally valid assumption but it will
      work for now.  

   2. Pull the raw data with getTickData.  Don't worry about the quote-only rows as these will be 
      deleted from the DB soon.

   3. Now have a pile of data by ticks and want to transform it for charting, analysis, etc.  How
      can this be done?

      a. Linear:
	 Iterate through the tick data and accumulate.  Set a ticks/period size (ex: 133), start at 0,
	 take a data row and add it to the current accumulation buffer.  Keep doing this until exceed
	 period size, store that data then start a new bar.  Bars should be by number of transactions
         (buy/sell).

         Does this actually work?  What if we miss transactions?  There might be a whole pile of them
         in between when we receive data. If we pull fast enough can it work?  Is there ANY way to know
  	 the number of transactions?  It can't be just volume.	 

      b. Volume
	 Yes, can aggregate on just volume.  Probably it will make a reasonable chart.  No matter what,
 	 it will allow creating the aggregation code, which should then work when we have data that
 	 will let us aggregate ticks.


> I'd really like to write about this.  What I am learning, what works what doesn't.  Software, data,
  technology.







Data and Tick Aggregation    
-----------------------------------------------------------------------------------------------------------------------
TL;DR: We can't aggregate ticks because we don't have real tick data.  Buy from someone else and use TD for other 
things -- historical minute, news, etc.  TD has limit of 1/2 sec pull frequency.


tick chart shows quantity of trades.  how can we get this?  Does ANY realtime tick data
show the number of trades that happened since the last tick?

timetx                 |timequote              |symbol|symbolactive|o      |h      |l      |c      |mark   |last   |bid    |ask    |lastsize|bidsize|asksize|volume|openint|
-----------------------+-----------------------+------+------------+-------+-------+-------+-------+-------+-------+-------+-------+--------+-------+-------+------+-------+
2021-10-18 18:20:45.093|2021-10-18 18:20:45.095|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4481.75|4481.75|4481.75|4482.00|       1|      5|     20| 17543|2303795|
2021-10-18 18:20:45.093|2021-10-18 18:20:46.328|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4481.75|4481.75|4481.50|4481.75|       1|     71|      5| 17543|2303795|17543
2021-10-18 18:20:41.334|2021-10-18 18:20:42.160|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4481.75|4481.75|4481.75|4482.00|       1|      7|     21| 17542|2303795|17542
2021-10-18 18:20:41.334|2021-10-18 18:20:42.784|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4481.75|4481.75|4481.75|4482.00|       1|      6|     21| 17542|2303795|17542
2021-10-18 18:20:40.182|2021-10-18 18:20:40.183|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4482.00|4481.75|4481.50|4481.75|      10|     73|      1| 17541|2303795|17536
2021-10-18 18:20:38.718|2021-10-18 18:20:39.115|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4481.75|4481.75|4481.50|4482.00|       1|     73|     27| 17526|2303795|17526
2021-10-18 18:20:38.176|2021-10-18 18:20:38.178|/ES   |/ESZ21      |4475.75|4484.25|4471.75|4477.50|4481.75|4481.75|4481.75|4482.00|       1|      4|     25| 17525|2303795|


This doesn't work to aggregate ticks unless we know how many transactions happened.  Otherwise how can we make a tick chart?  This is bad...but do things work the same with something
like a 1/2 second chart?  A 1/2 second chart could be close but this is going to have to be aggregated to something.  Somehow TOS is receiving a "number of transactions" counter
that the API is not giving us.  Now I am curious if other so called tick data APIs deliver this data or not.  Ideally it should give a number of transactions since the last row, or
it should actually make it possible to pull transactions, though this may require considerable bandwidth.

1) shop for a realtime tick data source that has API access and the we can try for not too much money
   IB, IQFeed, nanex.  Some of these send tick summaries, which is fine as long as the tick information is present.
   nanex/nxcore is apparently $300/month for CME.  This is reasonable if we are making ANY money.
   https://www.elitetrader.com/et/threads/nanex-nxcore-is-excellent.342506/

   IQFeed is a potential alternative.  See if we have pricing for this.
   http://www.iqfeed.net/index.cfm?displayaction=developer&section=main
   This may be only $420/yr in which case that is crazy cheap.  Could start with that and upgrade to nanex later.
   https://www.dtn.com/wp-content/uploads/2019/05/IQFeed_FAQs.pdf

   It looks like the API fee is additional so:
   API			$420/yr

   Core Service		  95/month
   RT Futures/Options	  20
   L2 (if needed)	  20
   CME			  $40-116 depending on what we get.  This is still > $200 month but nanex probably has exchange fees too.  Can waive exchange fees, basically if paying them somewherer else.

   Monthly Core		  95
   RT Futurs/Options	  20
   L2			  20
   Exchange CME mini	  66 (or +50 get all CME)




   Ideally we can make money to pay for this before we have to start buying expensive data.  Also this isn't much more than the storage unit.


2) experiment with a faster pull schedule.  
   It works but cannot seem to exceed 120 API calls/minute which is fully saturated by 1/2 sec calls.  This means we can make fairly nice charts (1/2 sec or 1 sec) but we can't
   really aggregate ticks.  We *could* aggregate by volume ticks (133 contracts/bar, etc).  This will probably be somewhat similar, and may or may not give similar signals.  
   This is the most frustrating thing encountered so far.
























Idea Formation
---------------------------------------------------------------------------------------------------
* How to actually "do something" with data?
  1) realtime strategy evaluator:
     operates on one more symbols (probably one)
     receives streaming data
     primes itself with additional historical data (last n ticks)

  2) on data received, split and get the part of the dataframe that corresponds to 
     the symbol(s) we care about.

     create something, anything, from a tecnical analysis standpoint.  For example
     calculate a 15, 50, 100, 200 MA and emit a signal when they cross

     in order to emit signals, we need something that supports this concept.  it's possible
     that the python backtesting / ta libraries can help here.  it's also possible to receive
     the data in another language like C++ and operate on it there.

     specify extra publish channels to marketlink.  ideally read these from the CSV, but we
     aren't doing that yet

     1. Send multipart with b"ALL" (all symbols)
     2. For any additional channels configured, send separately.  No updating these for now they
        just happen.


  3) Start calculating some metadata
     avg velocity transactions/second?
     avg volume?
     avg velocity and volume by time of day block (premarket, open, late morning, etc)
     can we identify general market characteristics (trending, oscillating, etc)?

     tulip indicators (written in C) vs Python TA lib.

