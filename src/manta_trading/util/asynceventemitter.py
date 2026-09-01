import asyncio

from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class AsyncEventEmitter:
    """
    Provides async event handling beyond what is present in asyncio.event.  Attempted to use
    PyPi package asyncio-events, but it is completely unusable (won't import, no stars, no examples)
    """

    def __init__(self):
        self.events = {}

    def on(self, event, callback):
        if event not in self.events:
            self.events[event] = []
        self.events[event].append(callback)

    async def emit(self, event, *args, **kwargs):
        """
        Emit an event with arguments to all registered callbacks.
        If a callback fails, it logs the error but continues executing other callbacks,
        preventing a single failing callback from breaking the entire event chain.
        """
        if event in self.events:
            results = []
            errors = []

            for callback in self.events[event]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        result = await callback(*args, **kwargs)
                    else:
                        result = callback(*args, **kwargs)
                    results.append(result)
                except Exception as e:
                    import traceback

                    _logger.error(
                        "Error in event callback for event '%s': %s", event, str(e)
                    )
                    _logger.debug("%s", traceback.format_exc())
                    errors.append(e)

            # Return a tuple of (results, errors) so caller can handle errors if needed
            return results, errors

    def remove_listener(self, event, callback):
        if event in self.events:
            self.events[event].remove(callback)
