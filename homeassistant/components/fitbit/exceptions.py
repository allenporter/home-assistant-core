"""Exceptions for fitbit API calls.

These exceptions exist to provide common exceptions for the async and sync client libraries.
"""

from fitbit_web_api.rest import ApiException

from homeassistant.exceptions import HomeAssistantError


class FitbitApiException(HomeAssistantError):
    """Error talking to the fitbit API."""

    def __init__(self, message: str, cause: ApiException | None = None) -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.cause = cause


class FitbitAuthException(FitbitApiException):
    """Authentication related error talking to the fitbit API."""
