
# Summary
This is an early-stage project to collect and analyze market data, and eventually to execute trades based on this data.  We will work on the following:

Structure:
* The project structure is contained in the directory_tree file so you will know where everything fits.

Data Collection:
* OHLC minute data: we have an AlphaVantage API subscription available for retrieving minute data for US equities, at least 10 years back.  We will pull 
    this data from AlphaVantage and store in flat CSV files (1 per symbol-month) and ingest them into a PostgresDB (probably) later.

* Tick Data: our primary data goal is to get to where we are collecting and ingesting tick data, especially for US futures.  We can buy one symbol-day for
    for $0.06 from QuantConnect, and will look at using this.

Data Analysis:
* The PineScript strategies will primarily be handled in another sub-project.  Our immediate focus here is on data collection.  Once we accomplish that,
  we will move on to data analysis.
