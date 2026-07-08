"""Typed config key definitions and defaults for persistent configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigKey:
    """Definition of a persistent configuration key."""

    name: str
    type_: type
    default: object
    description: str
    choices: tuple[str, ...] | None = None


CONFIG_KEYS: dict[str, ConfigKey] = {
    "default_provider": ConfigKey(
        name="default_provider",
        type_=str,
        default=None,
        description="Default data provider for commands",
    ),
    "output_format": ConfigKey(
        name="output_format",
        type_=str,
        default="text",
        description="Output format: text or json",
        choices=("text", "json"),
    ),
    "data_dir": ConfigKey(
        name="data_dir",
        type_=str,
        default=None,
        description="Base directory for local data files",
    ),
}


def get_default(key: str) -> object:
    """Return the default value for a config key.

    Raises KeyError if the key is not defined.
    """
    if key not in CONFIG_KEYS:
        raise KeyError(f"Unknown config key: {key}")
    return CONFIG_KEYS[key].default
