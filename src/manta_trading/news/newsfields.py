
# todo: can change this to NewsConstants and move the utility method here.
class NewsFields:
    DB_COLL_NEWS = 'news'
    DB_COLL_META = 'metadata'
    DB_META_LAST_UPDATED = 'newsLastUpdated'

    DB_ID = '_id'
    DB_API_SOURCE = 'api_source'
    DB_MARKET_TYPE = 'market-type'
    DB_NEWSITEMS = 'feed'
    DB_SENTIMENT = 'overall_sentiment_score'
    DB_SENTIMENT_LABEL = 'overall_sentiment_label'
    DB_SOURCE = 'source'
    DB_SUMMARY = 'summary'
    DB_TICKER = 'ticker_sentiment.ticker'
    DB_TIMESTAMP = 'time_published'
    DB_TIME_PUBLISHED = 'time_published'
    DB_TITLE = 'title'
    DB_TOPIC = 'topics.topic'
    DB_TYPE = 'type'

    # v2 metadata fields
    DB_META_TIMESTAMP = 'timestamp'
    DB_META_STATUS = 'status'
    DB_META_EARLIEST = 'earliest'
    DB_META_LATEST = 'latest'
    DB_META_TARGET = 'target'
    DB_META_COMPLETE = 'complete'

    DB_UPDATE_TYPE_HISTORICAL = 'newsUpdateHistorical'
    DB_UPDATE_TYPE_CURRENT = 'newsUpdateCurrent'

    # v1 metadata fields
    DB_V1_READ_TIMESTAMP = 'readStatusTimestamp'           # 'timestampHistoricalComplete', 'timestampLastComplete'
    DB_V1_READ_COMPLETE = 'readStatusComplete'
    DB_V1_READ_EARLIEST = 'readStatusEarliest'             # 'timestampHistoricalEarliest', 'timestampLastEarliest'
    DB_V1_READ_LATEST = 'readStatusLatest'
    DB_V1_READ_TARGET = 'readStatusTarget'
    DB_V1_READ_STATUS = 'readStatus'


    
    # mappings are from the old hist columns to the new db read columns