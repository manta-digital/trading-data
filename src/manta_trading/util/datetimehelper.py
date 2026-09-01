import pytz

from datetime import datetime, timezone, date, time
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta


class DateTimeHelper:
    DEFAULT_FORMAT = '%Y-%m-%d %H:%M:%S'
    DATE_FORMAT = '%Y-%m-%d'
    API_FORMAT = '%Y%m%dT%H%M'
    DB_FORMAT_ISO='iso8601'
    DB_FORMAT = '%Y%m%dT%H%M'
    DB_FORMAT_SECONDS = '%Y%m%dT%H%M%S'

    @staticmethod
    def toDateTime(timestamp, tz=timezone.utc):
        """
        Accepts a string or datetime input.  If string, attempts to parse using known formats.
        If datetime, ensure result is returned in specified timezone.
        """
        result = None

        if timestamp is None:
            return None

        elif isinstance(timestamp, datetime):
            result = timestamp

        elif isinstance(timestamp, int):
            result = DateTimeHelper.fromUtcTimestamp(timestamp)

        elif not isinstance(timestamp, str):
            raise ValueError(f"Invalid timestamp type: {type(timestamp)}")

        # Attempt to parse from ISO-8601 string since we use those the most.
        else:
            try:
                result = datetime.fromisoformat(timestamp)
            except Exception:
                pass

        if result is None:
            possible_formats = [
                '%Y-%m-%d',
                '%Y%m%dT%H%M%S',
                '%Y%m%dT%H%M',
                '%Y/%m/%d',
                '%d-%m-%Y',
                '%d/%m/%Y',
                '%Y-%m-%d %H:%M:%S',
            ]

            for fmt in possible_formats:
                try:
                    dt = datetime.strptime(timestamp, fmt)
                    dt = dt.replace(microsecond=0)
                    result = dt
                    break
                except ValueError:
                    continue

        # If none of the formats match, try flexible parsing
        if result is None:
            try:
                dt = parse(timestamp)
                dt = dt.replace(microsecond=0)
                result = dt
            except ValueError:
                raise ValueError(f"Unable to parse timestamp: {timestamp}")

        # Localize to timezone
        if result is None:
            return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=tz)
        elif result.tzinfo != tz:
            result = result.astimezone(tz)
        return result

    @staticmethod
    def parseTimestampAsDatetime(timestamp):
        return DateTimeHelper.toDateTime(timestamp, tz=None)

    @staticmethod
    def toUtcTimestamp(dt, tz='UTC'):
        """Convert datetime, string, or int to UTC timestamp."""
        if isinstance(dt, int):
            return dt
        if isinstance(dt, str):
            dt = DateTimeHelper.parseTimestampAsDatetime(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.timezone(tz))
        return int(dt.timestamp())

    @staticmethod
    def fromUtcTimestamp(timestamp):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    @staticmethod
    def toIso8601(dt):
        """Convert datetime, string, or int to ISO 8601 format string."""
        if isinstance(dt, int):
            dt = datetime.fromtimestamp(dt, tz=timezone.utc)
        elif isinstance(dt, str):
            dt = DateTimeHelper.parseTimestampAsDatetime(dt)
        return dt.astimezone(timezone.utc).isoformat()

    @staticmethod
    def asDateTime(dateTest, format='%Y-%m-%d'):
        if dateTest is None:
            return None

        if isinstance(dateTest, str):
            return DateTimeHelper.parseTimestampAsDatetime(dateTest)

        elif isinstance(dateTest, datetime):
            return dateTest

        else:
            raise ValueError(f"Unexpected type for datetime value: {type(dateTest)}")

    @staticmethod
    def convertToApiFormatString(dt, format=API_FORMAT, tz='US/Eastern'):
        """Convert to API or other local/specific format (defaulting to AlphaVantage format)."""
        if isinstance(dt, str):
            dt = DateTimeHelper.parseTimestampAsDatetime(dt)
        local_tz = pytz.timezone(tz)
        localized_dt = dt.astimezone(local_tz)
        return localized_dt.strftime(format)

    @staticmethod
    def convertToDbFormatString(dt, format=DB_FORMAT_ISO):
        """Convert datetime to string in specified format (default: ISO-8601)."""
        if dt is None:
            return None
        if isinstance(dt, str):
            dt = DateTimeHelper.parseTimestampAsDatetime(dt)
        if format.lower() == 'iso8601':
            return dt.astimezone(timezone.utc).isoformat()
        return dt.strftime(format)

    @staticmethod
    def applyDateTimeDefaults(value):
        """
        Ensures the given value is a datetime object with appropriate timezone info.

        :param value: Input value (datetime, string, int, or date)
        :return: Processed datetime object
        """
        if value is None:
            return None

        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, time.min)

        elif isinstance(value, time):
            value = datetime.combine(date.today(), value)

        elif not isinstance(value, (datetime, str, int)):
            raise ValueError(f"Unsupported type for datetime conversion: {type(value)}")

        # Convert to datetime if not already
        if not isinstance(value, datetime):
            value = DateTimeHelper.parseTimestampAsDatetime(value)

        # If it's a datetime with no timezone, interpret as local time
        if value.tzinfo is None:
            if value.time() == time.min:
                return value.replace(tzinfo=timezone.utc)
            else:
                local_dt = datetime.now(timezone.utc).astimezone().tzinfo
                value = value.replace(tzinfo=local_dt)

        return value.astimezone(timezone.utc)

    @staticmethod
    def getDefaultDateInterval(date_from, date_to):
        """Get default date interval."""
        if date_from is None:
            date_from = '2020-01-01'

        if date_to is None:
            date_from_obj = datetime.strptime(date_from, "%Y-%m-%d")
            date_to_obj = date_from_obj + relativedelta(years=1, days=-1)
            date_to = date_to_obj.strftime("%Y-%m-%d")

        return date_from, date_to
