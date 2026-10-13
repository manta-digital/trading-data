"""API unit tests: never open a pool against the developer's tick database."""

from tick_support.api import no_dev_tick_settings  # noqa: F401  (autouse guard)
