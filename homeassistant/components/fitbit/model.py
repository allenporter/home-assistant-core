"""Data representation for fitbit API responses."""

from dataclasses import dataclass


@dataclass
class FitbitProfile:
    """User profile from the Fitbit API response."""

    encoded_id: str
    """The ID representing the Fitbit user."""

    full_name: str
    """The first name value specified in the user's account settings."""


@dataclass
class FitbitDevice:
    """Device from the Fitbit API response."""

    id: str
    """The device ID."""

    device_version: str
    """The product name of the device."""

    battery_level: int
    """The battery level as a percentage."""

    type: str
    """The type of the device such as TRACKER or SCALE."""
