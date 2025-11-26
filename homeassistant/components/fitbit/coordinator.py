"""Coordinator for fetching data from fitbit API."""

import asyncio
from dataclasses import dataclass
import datetime
import logging
from typing import Any, Final

from fitbit_web_api.models.device import Device
from fitbit_web_api.models.get_sleep_log_by_date_response import (
    GetSleepLogByDateResponse,
)
from fitbit_web_api.models.get_body_fat_log_response import GetBodyFatLogResponse
from fitbit_web_api.models.get_weight_log_response import GetWeightLogResponse
from fitbit_web_api.models.get_sleep_log_list_response import (
    GetSleepLogListResponse,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import FitbitScope
from .api import FitbitApi
from .exceptions import FitbitApiException, FitbitAuthException
from .model import config_from_entry_data

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL: Final = datetime.timedelta(minutes=30)
TIMEOUT = 10

type FitbitConfigEntry = ConfigEntry[FitbitRuntimeConfig]


@dataclass
class FitbitData:
    """Config Entry global data."""

    devices: list[Device] | None
    sleep_log: GetSleepLogListResponse | None
    body_fat: GetBodyFatLogResponse | None
    weight: GetWeightLogResponse | None


class FitbitDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for fetching fitbit devices from the API."""

    config_entry: FitbitConfigEntry

    def __init__(
        self, hass: HomeAssistant, config_entry: FitbitConfigEntry, api: FitbitApi
    ) -> None:
        """Initialize FitbitDeviceCoordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Fitbit",
            update_interval=UPDATE_INTERVAL,
        )
        self._api = api
        self._config = config_from_entry_data(config_entry.data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        async with asyncio.timeout(TIMEOUT):
            tasks = []
            if self._config.is_allowed_resource(FitbitScope.DEVICE, "devices/battery"):
                tasks.append(self._api.async_get_devices())
            else:
                tasks.append(asyncio.sleep(0, result=None))
            if self._config.is_allowed_resource(FitbitScope.SLEEP, "sleep"):
                tasks.append(self._api.async_get_sleep_log())
            else:
                tasks.append(asyncio.sleep(0, result=None))
            if self._config.is_allowed_resource(FitbitScope.WEIGHT, "body/fat"):
                tasks.append(self._api.async_get_body_fat_log())
            else:
                tasks.append(asyncio.sleep(0, result=None))
            if self._config.is_allowed_resource(FitbitScope.WEIGHT, "body/weight"):
                tasks.append(self._api.async_get_weight_log())
            else:
                tasks.append(asyncio.sleep(0, result=None))
            try:
                (devices, sleep_log, body_fat, weight) = await asyncio.gather(*tasks)
            except FitbitAuthException as err:
                raise ConfigEntryAuthFailed(err) from err
            except FitbitApiException as err:
                raise UpdateFailed(err) from err
        return FitbitData(
            devices=devices,
            sleep_log=sleep_log,
            body_fat=body_fat,
            weight=weight,
        )


@dataclass
class FitbitContext:
    """Config Entry global data."""

    api: FitbitApi
    coordinator: FitbitDataCoordinator | None
