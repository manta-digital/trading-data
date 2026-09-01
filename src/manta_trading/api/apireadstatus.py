from enum import Enum
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


# Common API read result statuses.
class ApiReadStatus(Enum):
    SUCCESS = 0
    API_INVALID_CALL = 2
    API_NO_DATA = 3
    API_NO_RESPONSE = 4
    API_SYMBOL_NOT_FOUND = 5
    API_RATE_LIMIT = 6
    PROCESSING_ERROR = 7
    VALIDATION_ERROR = 8
    TIMEOUT_ERROR = 9
    NETWORK_ERROR = 10
    UNAUTHORIZED = 11
    NOT_FOUND = 12
    DB_ERROR = 13


class ErrorHandler:
    """
    Standard error handling utilities for the application.
    Provides consistent methods for error creation, logging, and handling.
    """

    @staticmethod
    def create_error(error_type, message=None, details=None):
        """
        Create a standardized error dictionary.

        Args:
            error_type: Either an ApiReadStatus enum or a string
            message: Human-readable error message
            details: Additional error details or exception information

        Returns:
            dict: Standardized error dictionary
        """
        if isinstance(error_type, ApiReadStatus):
            error_code = error_type.value
        else:
            error_code = str(error_type)

        error = {
            "error": error_code,
            "message": message or f"An error of type {error_code} occurred",
        }

        if details:
            error["details"] = details

        return error

    @staticmethod
    def log_error(error_dict, logger_instance=None, log_level="error"):
        """
        Log an error dictionary with appropriate level.

        Args:
            error_dict: Error dictionary to log
            logger_instance: Logger to use (defaults to module logger)
            log_level: Logging level ('error', 'warning', 'info')
        """
        log = logger_instance if logger_instance is not None else _logger
        message = "%s: %s" % (error_dict.get("error"), error_dict.get("message"))
        if "details" in error_dict:
            message += " - %s" % error_dict["details"]

        if log_level == "error":
            log.error("%s", message)
        elif log_level == "warning":
            log.warning("%s", message)
        else:
            log.info("%s", message)

    @staticmethod
    def handle_exception(e, context=None, expected_exceptions=None, log_level="error"):
        """
        Handle an exception and convert it to a standardized error dict.

        Args:
            e: The exception
            context: Context information (e.g., symbol, function name)
            expected_exceptions: Dict mapping exception types to ApiReadStatus values
            log_level: Level to log at ('error', 'warning', 'info')

        Returns:
            dict: Standardized error dictionary
        """
        error_type = ApiReadStatus.PROCESSING_ERROR

        # Map known exception types to corresponding error status
        if expected_exceptions:
            for exc_type, err_status in expected_exceptions.items():
                if isinstance(e, exc_type):
                    error_type = err_status
                    break

        # Build context string
        context_str = ""
        if context:
            if isinstance(context, str):
                context_str = context
            elif isinstance(context, dict):
                context_str = " ".join([f"{k}={v}" for k, v in context.items()])

        # Create error dict
        error = ErrorHandler.create_error(
            error_type, message=str(e), details=context_str if context_str else None
        )

        # Log it
        ErrorHandler.log_error(error, log_level=log_level)

        return error

    @staticmethod
    def is_error(result):
        """
        Check if a result is an error dictionary.

        Args:
            result: Result to check

        Returns:
            bool: True if result is an error dictionary
        """
        return isinstance(result, dict) and "error" in result
