
import hashlib
from manta_trading.news.newsfields import NewsFields


class NewsUtility:

    # Generate a hash for the article.  Initial method of minimizing duplicate articles.
    @staticmethod
    def generateArticleHash(item):
        if NewsFields.DB_TIME_PUBLISHED not in item or NewsFields.DB_SUMMARY not in item:
            return None

        unique_string = f"{item[NewsFields.DB_TIME_PUBLISHED][:11]}{item[NewsFields.DB_SENTIMENT]}{item[NewsFields.DB_SUMMARY][:64]}"
        return hashlib.md5(unique_string.encode()).hexdigest()

