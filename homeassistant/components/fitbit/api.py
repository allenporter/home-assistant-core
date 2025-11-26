"""API for fitbit bound to Home Assistant OAuth."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Awaitable
import datetime
import logging
from typing import Any

from fitbit_web_api import (
    ApiClient,
    Configuration,
    GetHeartRateTimeSeriesResponse,
    HeartRateTimeSeriesApi,
    BodyTimeSeriesApi,
    SleepApi,
)
from fitbit_web_api.exceptions import (
    OpenApiException,
    ApiException,
    UnauthorizedException,
)
from fitbit_web_api.api.devices_api import DevicesApi
from fitbit_web_api.api.user_api import UserApi
from fitbit_web_api.api.activity_time_series_api import ActivityTimeSeriesApi
from fitbit_web_api.api.body_api import BodyApi
from fitbit_web_api.models.device import Device
from fitbit_web_api.models.user import User
from fitbit_web_api.models.get_activity_time_series_response import (
    GetActivityTimeSeriesResponse,
)
from fitbit_web_api.models.get_weight_log_response import GetWeightLogResponse
from fitbit_web_api.models.get_body_fat_log_response import GetBodyFatLogResponse
from fitbit_web_api.models.get_body_time_series_response import (
    GetBodyTimeSeriesResponse,
)
from fitbit_web_api.models.get_sleep_log_list_response import (
    GetSleepLogListResponse,
)
from fitbit_web_api.models.sleep_summary import SleepSummary

from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.unit_system import METRIC_SYSTEM

from .const import FitbitUnitSystem, FitbitScope
from .exceptions import FitbitAuthException, FitbitApiException

_LOGGER = logging.getLogger(__name__)

CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"
# Allow resource paths like 'tracker/steps' to be replaced without encoding
SAFE_CHARS_FOR_PATH_PARAM = "/"


class FitbitApi(ABC):
    """Fitbit client library wrapper base class.

    This can be subclassed with different implementations for providing an access
    token depending on the use case.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        unit_system: FitbitUnitSystem | None = None,
    ) -> None:
        """Initialize Fitbit auth."""
        self._hass = hass
        self._profile: Any | None = None
        self._unit_system = unit_system

    @abstractmethod
    async def async_get_access_token(self) -> dict[str, Any]:
        """Return a valid token dictionary for the Fitbit API."""

    async def _async_get_client(self) -> ApiClient:
        """Create and return an ApiClient configured with the current access token."""
        token = await self.async_get_access_token()
        configuration = Configuration()
        configuration.pool_manager = async_get_clientsession(self._hass)
        configuration.safe_chars_for_path_param = SAFE_CHARS_FOR_PATH_PARAM
        configuration.access_token = token[CONF_ACCESS_TOKEN]
        return ApiClient(configuration)

    async def async_get_user_profile(self) -> User:
        """Return the user profile from the API using fitbit-web-api."""
        if self._profile is None:
            client = await self._async_get_client()
            user_api = UserApi(client)
            response = await self._run(user_api.get_profile())
            _LOGGER.debug("get_profile=%s", response)
            self._profile = response.user
        return self._profile

    async def async_get_unit_system(self) -> FitbitUnitSystem:
        """Get the unit system to use when fetching timeseries.

        This is used in a couple ways. The first is to determine the request
        header to use when talking to the fitbit API which changes the
        units returned by the API. The second is to tell Home Assistant the
        units set in sensor values for the values returned by the API.
        """
        if (
            self._unit_system is not None
            and self._unit_system != FitbitUnitSystem.LEGACY_DEFAULT
        ):
            return self._unit_system
        # Use units consistent with the account user profile or fallback to the
        # home assistant unit settings.
        profile = await self.async_get_user_profile()
        if profile.locale == FitbitUnitSystem.EN_GB:
            return FitbitUnitSystem.EN_GB
        if self._hass.config.units is METRIC_SYSTEM:
            return FitbitUnitSystem.METRIC
        return FitbitUnitSystem.EN_US

    async def async_get_devices(self) -> list[Device]:
        """Return available devices using fitbit-web-api."""
        client = await self._async_get_client()
        devices_api = DevicesApi(client)
        devices = await self._run(devices_api.get_devices())
        _LOGGER.debug("get_devices=%s", devices)
        return devices

    async def async_get_activities(
        self, resource_type: str
    ) -> GetActivityTimeSeriesResponse:
        """Return recent activities from the time series for the specified resource type."""
        client = await self._async_get_client()
        headers = await self._async_get_headers()
        api_instance = ActivityTimeSeriesApi(client)
        _LOGGER.debug("Fetching time series for resource_type=%s", resource_type)
        api_func = api_instance.get_activities_resource_by_date_period(
            var_resource_path=resource_type,
            var_date="today",
            period="7d",
            _headers=headers,
        )
        return await self._run(api_func)

    async def async_get_body_fat_log(self) -> GetBodyFatLogResponse:
        """Return body fat data from the Fitbit API."""
        client = await self._async_get_client()
        headers = await self._async_get_headers()
        api_instance = BodyApi(client)
        api_func = api_instance.get_body_fat_by_date(
            var_date=datetime.date.today(),
            _headers=headers,
        )
        return await self._run(api_func)

    async def async_get_weight_log(self) -> GetWeightLogResponse:
        """Return weight data from the Fitbit API."""
        client = await self._async_get_client()
        headers = await self._async_get_headers()
        api_instance = BodyApi(client)
        api_func = api_instance.get_weight_by_date(
            var_date=datetime.date.today(),
            _headers=headers,
        )
        return await self._run(api_func)

    async def async_get_heart_rate(self) -> GetHeartRateTimeSeriesResponse:
        """Return recent heart rate data from the time series."""
        client = await self._async_get_client()
        headers = await self._async_get_headers()
        api_instance = HeartRateTimeSeriesApi(client)
        _LOGGER.debug("Fetching heart rate time series")
        api_func = api_instance.get_heart_by_date_period(
            var_date="today",
            period="7d",
            _headers=headers,
        )
        return await self._run(api_func)

    async def async_get_sleep_log(self) -> GetSleepLogListResponse:
        """Return sleep data from the Fitbit API."""
        client = await self._async_get_client()
        headers = await self._async_get_headers()
        api_instance = SleepApi(client)
        api_func = api_instance.get_sleep_list(
            sort="desc",
            limit=1,
            before_date=(datetime.date.today() + datetime.timedelta(days=1)),
            _headers=headers,
        )
        return await self._run(api_func)

    async def _async_get_headers(self) -> dict[str, str]:
        """Get headers including unit system for requests to Fitbit API."""
        unit_system = await self.async_get_unit_system()
        return {
            "Accept-Language": unit_system.value,
            "Accept-Locale": unit_system.value,
        }

    async def _run[_T](self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run client command."""
        try:
            return await func
        except UnauthorizedException as err:
            _LOGGER.debug("Unauthorized error from fitbit API: %s", err)
            raise FitbitAuthException("Authentication error from fitbit API") from err
        except ApiException as err:
            _LOGGER.debug("Error from fitbit API: %s", err)
            raise FitbitApiException("Error from fitbit API") from err
        except OpenApiException as err:
            _LOGGER.debug("Connection communicating with fitbit API: %s", err)
            raise FitbitApiException("Communication error from fitbit API") from err


class OAuthFitbitApi(FitbitApi):
    """Provide fitbit authentication tied to an OAuth2 based config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
        unit_system: FitbitUnitSystem | None = None,
    ) -> None:
        """Initialize OAuthFitbitApi."""
        super().__init__(hass, unit_system)
        self._oauth_session = oauth_session

    async def async_get_access_token(self) -> dict[str, Any]:
        """Return a valid access token for the Fitbit API."""
        await self._oauth_session.async_ensure_token_valid()
        return self._oauth_session.token


class ConfigFlowFitbitApi(FitbitApi):
    """Profile fitbit authentication before a ConfigEntry exists.

    This implementation directly provides the token without supporting refresh.
    """

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
