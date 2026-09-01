import os
from datetime import datetime
from dateutil.parser import parse
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class PathUtil:
    @staticmethod
    def createDirectories(path):

        try:
            # Split the path in directory and file
            directory = os.path.dirname(path)

            # Check if the directory exists and create it if necessary
            if not os.path.exists(directory):
                os.makedirs(directory)

        except PermissionError:
            _logger.error("Permission error creating directory: %s", directory)
        except Exception as e:
            _logger.error("Unknown error creating directory: %s", e)

    def generateMergedOutputFilename(
        symbol=None, date=None, outputPath=None, dateFormat=None
    ):
        fileExtension = "csv"

        if isinstance(date, str):
            try:
                date_obj = parse(date)  # Flexibly parse the date string
                date_str = date_obj.strftime(dateFormat)
            except ValueError:
                _logger.error(
                    "Invalid date format: %s. Please use the format: %s",
                    date,
                    dateFormat,
                )
                return None
        elif isinstance(date, datetime):
            date_str = date.strftime(dateFormat)
        else:
            _logger.error("Date must be either a string or a datetime object.")
            return None

        index = 0  # Start index at 0 for the first run
        while True:
            if index == 0:
                filename = f"{symbol}-{date_str}.{fileExtension}"
            else:
                filename = f"{symbol}-{date_str}-{index}.{fileExtension}"
            file_path = (
                filename if outputPath is None else os.path.join(outputPath, filename)
            )

            # Check if the file already exists. If it doesn't, break out of the loop.
            if not os.path.exists(file_path):
                break
            index += 1  # Increment the index if the file exists.

        return file_path
