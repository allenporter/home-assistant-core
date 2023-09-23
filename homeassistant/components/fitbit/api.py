"""API for fitbit bound to Home Assistant OAuth."""

from abc import ABC, abstractmethod
import logging
from typing import Any, cast

from fitbit import Fitbit

from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .model import FitbitDevice, FitbitProfile

_LOGGER = logging.getLogger(__name__)

CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"


class FitbitApi(ABC):
    """Fitbit client library wrapper base class."""

    def __init__(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Initialize Fitbit auth."""
        self._hass = hass
        self._profile: FitbitProfile | None = None

    @abstractmethod
    async def async_get_access_token(self) -> dict[str, Any]:
        """Return a valid token dictionary for the Fitbit API."""

    async def _async_get_client(self) -> Fitbit:
        """Get synchronous client library, called before each client request."""
        # Always rely on Home Assistant's token update mechanism which refreshes
        # the data in the configuration entry.
        token = await self.async_get_access_token()
        return Fitbit(
            client_id=None,
            client_secret=None,
            access_token=token[CONF_ACCESS_TOKEN],
            refresh_token=token[CONF_REFRESH_TOKEN],
            expires_at=float(token[CONF_EXPIRES_AT]),
        )

    async def async_get_user_profile(self) -> FitbitProfile:
        """Return the user profile from the API."""
        client = await self._async_get_client()
        response: dict[str, Any] = await self._hass.async_add_executor_job(
            client.user_profile_get
        )
        _LOGGER.debug("user_profile_get=%s", response)
        profile = response["user"]
        return FitbitProfile(
            encoded_id=profile["encodedId"], full_name=profile["fullName"]
        )

    async def async_get_devices(self) -> list[FitbitDevice]:
        """Return available devices."""
        client = await self._async_get_client()
        devices: list[dict[str, str]] = await self._hass.async_add_executor_job(
            client.get_devices
        )
        _LOGGER.debug("get_devices=%s", devices)
        results: list[FitbitDevice] = []
        for device in devices:
            results.append(
                FitbitDevice(
                    id=device["id"],
                    device_version=device["deviceVersion"],
                    battery_level=int(device["batteryLevel"]),
                    type=device["type"],
                )
            )
        return results

    async def async_get_latest_time_series(self, resource_type: str) -> str | None:
        """Return the most recent value from the time series for the specified resource type."""
        client = await self._async_get_client()
        container = resource_type.replace("/", "-")

        def _time_series() -> dict[str, Any]:
            return cast(dict[str, Any], client.time_series(resource_type, period="7d"))

        response: dict[str, Any] = await self._hass.async_add_executor_job(_time_series)
        _LOGGER.debug("time_series(%s)=%s", resource_type, response)
        return cast(str | None, response[container][-1].get("value"))


class OAuthFitbitApi(FitbitApi):
    """Provide fitbit authentication tied to an OAuth2 based config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize OAuthFitbitApi."""
        super().__init__(hass)
        self._oauth_session = oauth_session

    async def async_get_access_token(self) -> dict[str, Any]:
        """Return a valid access token for the Fitbit API."""
        if not self._oauth_session.valid_token:
            await self._oauth_session.async_ensure_token_valid()
        return self._oauth_session.token


class ConfigFlowFitbitApi(FitbitApi):
    """Profile fitbit authentication before a ConfigEntry exists."""

    def __init__(
        self,
        hass: HomeAssistant,
        token: dict[str, Any],
    ) -> None:
        """Initialize ConfigFlowFitbitApi."""
        super().__init__(hass)
        self._token = token

    async def async_get_access_token(self) -> dict[str, Any]:
        """Return the token for the Fitbit API."""
        return self._token
