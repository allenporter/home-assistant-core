"""Support for the Fitbit API."""
# pylint: disable=fixme
from __future__ import annotations

from dataclasses import dataclass
import datetime
import logging
import os
from typing import Final

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as PARENT_PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_UNIT_SYSTEM, PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.json import load_json_object

from .api import FitbitApi
from .const import (
    ATTRIBUTION,
    CONF_CLOCK_FORMAT,
    CONF_MONITORED_RESOURCES,
    DEFAULT_CLOCK_FORMAT,
    DEFAULT_CONFIG,
    DEVICE_MANUFACTURER,
    DOMAIN,
    FITBIT_CONFIG_FILE,
    FITBIT_DEFAULT_RESOURCES,
)
from .model import FitbitDevice, FitbitProfile

_LOGGER: Final = logging.getLogger(__name__)

_CONFIGURING: dict[str, str] = {}

SCAN_INTERVAL: Final = datetime.timedelta(minutes=30)


@dataclass
class FitbitSensorEntityDescription(SensorEntityDescription):
    """Describes Fitbit sensor entity."""

    unit_type: str | None = None


FITBIT_RESOURCES_LIST: Final[tuple[FitbitSensorEntityDescription, ...]] = (
    FitbitSensorEntityDescription(
        key="activities/activityCalories",
        name="Activity Calories",
        native_unit_of_measurement="cal",
        icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/calories",
        name="Calories",
        native_unit_of_measurement="cal",
        icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/caloriesBMR",
        name="Calories BMR",
        native_unit_of_measurement="cal",
        icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/distance",
        name="Distance",
        unit_type="distance",
        icon="mdi:map-marker",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/elevation",
        name="Elevation",
        unit_type="elevation",
        icon="mdi:walk",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/floors",
        name="Floors",
        native_unit_of_measurement="floors",
        icon="mdi:walk",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/heart",
        name="Resting Heart Rate",
        native_unit_of_measurement="bpm",
        icon="mdi:heart-pulse",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/minutesFairlyActive",
        name="Minutes Fairly Active",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:walk",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/minutesLightlyActive",
        name="Minutes Lightly Active",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:walk",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/minutesSedentary",
        name="Minutes Sedentary",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:seat-recline-normal",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/minutesVeryActive",
        name="Minutes Very Active",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:run",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/steps",
        name="Steps",
        native_unit_of_measurement="steps",
        icon="mdi:walk",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/activityCalories",
        name="Tracker Activity Calories",
        native_unit_of_measurement="cal",
        icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/calories",
        name="Tracker Calories",
        native_unit_of_measurement="cal",
        icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/distance",
        name="Tracker Distance",
        unit_type="distance",
        icon="mdi:map-marker",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/elevation",
        name="Tracker Elevation",
        unit_type="elevation",
        icon="mdi:walk",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/floors",
        name="Tracker Floors",
        native_unit_of_measurement="floors",
        icon="mdi:walk",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/minutesFairlyActive",
        name="Tracker Minutes Fairly Active",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:walk",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/minutesLightlyActive",
        name="Tracker Minutes Lightly Active",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:walk",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/minutesSedentary",
        name="Tracker Minutes Sedentary",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:seat-recline-normal",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/minutesVeryActive",
        name="Tracker Minutes Very Active",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:run",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="activities/tracker/steps",
        name="Tracker Steps",
        native_unit_of_measurement="steps",
        icon="mdi:walk",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="body/bmi",
        name="BMI",
        native_unit_of_measurement="BMI",
        icon="mdi:human",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="body/fat",
        name="Body Fat",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:human",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="body/weight",
        name="Weight",
        unit_type="weight",
        icon="mdi:human",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.WEIGHT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/awakeningsCount",
        name="Awakenings Count",
        native_unit_of_measurement="times awaken",
        icon="mdi:sleep",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/efficiency",
        name="Sleep Efficiency",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:sleep",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/minutesAfterWakeup",
        name="Minutes After Wakeup",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:sleep",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/minutesAsleep",
        name="Sleep Minutes Asleep",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:sleep",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/minutesAwake",
        name="Sleep Minutes Awake",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:sleep",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/minutesToFallAsleep",
        name="Sleep Minutes to Fall Asleep",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:sleep",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/startTime",
        name="Sleep Start Time",
        icon="mdi:clock",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FitbitSensorEntityDescription(
        key="sleep/timeInBed",
        name="Sleep Time in Bed",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:hotel",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

FITBIT_RESOURCE_BATTERY = FitbitSensorEntityDescription(
    key="devices/battery",
    translation_key="battery",
    has_entity_name=True,
    device_class=SensorDeviceClass.BATTERY,
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
)

FITBIT_RESOURCES_KEYS: Final[list[str]] = [
    desc.key for desc in (*FITBIT_RESOURCES_LIST, FITBIT_RESOURCE_BATTERY)
]

PLATFORM_SCHEMA: Final = PARENT_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(
            CONF_MONITORED_RESOURCES, default=FITBIT_DEFAULT_RESOURCES
        ): vol.All(cv.ensure_list, [vol.In(FITBIT_RESOURCES_KEYS)]),
        vol.Optional(CONF_CLOCK_FORMAT, default=DEFAULT_CLOCK_FORMAT): vol.In(
            ["12H", "24H"]
        ),
        vol.Optional(CONF_UNIT_SYSTEM, default="default"): vol.In(
            ["en_GB", "en_US", "metric", "default"]
        ),
    }
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Fitbit sensor."""
    config_path = hass.config.path(FITBIT_CONFIG_FILE)
    if os.path.isfile(config_path):
        config_file = load_json_object(config_path)
        if config_file == DEFAULT_CONFIG:
            # Has entry in configuration.yaml, but integration has not yet been configured
            # TODO: File a repair issue: Integration can now be configured from the UI
            # TODO: Repair issue saying can be removed from configuration.yaml
            return
        # TODO: Also handle the same case if missing ATTR_ACCESS_TOKEN, ATTR_REFRESH_TOKEN, ATTR_LAST_SAVED_AT
    else:
        # Has entry in configuration.yaml, but integration has not yet been configured
        # TODO: File a repair issue: Integration can now be configured from the UI
        # TODO: Repair issue saying can be removed from configuration.yaml
        return

    # TODO: Have existing fitbit configuration. Import application credentials and create
    # a config entry, then file a repair issue that integration can now be configured
    # from the UI.
    # TODO: Remove fitbit.conf
    # TODO: Repair issue saying can be removed from configuration.yaml

    # User specified an explicit Unit system? Or just use home assistant
    # TODO: Import units preference
    # user_profile = authd_client.user_profile_get()["user"]
    # if (unit_system := config[CONF_UNIT_SYSTEM]) == "default":
    #     authd_client.system = user_profile["locale"]
    #     if authd_client.system != "en_GB":
    #         if hass.config.units is METRIC_SYSTEM:
    #             authd_client.system = "metric"
    #         else:
    #             authd_client.system = "en_US"
    # else:
    #     # User specified an explicit Unit system.
    #     authd_client.system = unit_system

    # registered_devs = authd_client.get_devices()
    # clock_format = config[CONF_CLOCK_FORMAT]
    # monitored_resources = config[CONF_MONITORED_RESOURCES]
    # TODO: Check  description.key against monitored_resources


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fitbit sensor platform."""

    api: FitbitApi = hass.data[DOMAIN][entry.entry_id]

    profile = await api.async_get_user_profile()
    devices = await api.async_get_devices()
    entities: list[SensorEntity] = []
    for device in devices:
        entities.append(FitbitBatterySensor(api, profile, device))
    for description in FITBIT_RESOURCES_LIST:
        if description.key not in FITBIT_RESOURCES_KEYS:  # TODO: Check scopes
            continue
        entities.append(
            FitbitSensor(
                api,
                profile,
                description,
            )
        )
    async_add_entities(entities)


class FitbitSensor(SensorEntity):
    """Implementation of a Fitbit sensor."""

    entity_description: FitbitSensorEntityDescription
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        api: FitbitApi,
        profile: FitbitProfile,
        description: FitbitSensorEntityDescription,
    ) -> None:
        """Initialize FitbitSensor."""
        self.entity_description = description
        self._api = api
        self._profile = profile
        self._attr_unique_id = f"{profile.encoded_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, profile.encoded_id)},
            name=profile.full_name,
            manufacturer=DEVICE_MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )
        # TODO: Support clock formats and unit_type
        self.clock_format = None

    async def async_update(self) -> None:
        """Get the latest data from the Fitbit API and update the states."""
        resource_type = self.entity_description.key
        raw_state = await self._api.async_get_latest_time_series(resource_type)
        if not raw_state:
            return None
        if resource_type == "activities/distance":
            self._attr_native_value = format(float(raw_state), ".2f")
        elif resource_type == "activities/tracker/distance":
            self._attr_native_value = format(float(raw_state), ".2f")
        elif resource_type == "body/bmi":
            self._attr_native_value = format(float(raw_state), ".1f")
        elif resource_type == "body/fat":
            self._attr_native_value = format(float(raw_state), ".1f")
        elif resource_type == "body/weight":
            self._attr_native_value = format(float(raw_state), ".1f")
        elif resource_type == "sleep/startTime":
            if raw_state == "":
                self._attr_native_value = "-"
            elif self.clock_format == "12H":
                hours_str, minutes_str = raw_state.split(":")
                hours, minutes = int(hours_str), int(minutes_str)
                setting = "AM"
                if hours > 12:
                    setting = "PM"
                    hours -= 12
                elif hours == 0:
                    hours = 12
                self._attr_native_value = f"{hours}:{minutes:02d} {setting}"
            else:
                self._attr_native_value = raw_state
        # TODO: Needs to fix unit conversions
        # elif self.is_metric:
        #     self._attr_native_value = raw_state
        else:
            try:
                self._attr_native_value = int(raw_state)
            except TypeError:
                self._attr_native_value = raw_state

        # Needs typing fix
        # if resource_type == "activities/heart":
        #    self._attr_native_value = raw_state.get("restingHeartRate")


class FitbitBatterySensor(SensorEntity):
    """Implementation of a Fitbit battery sensor."""

    entity_description = FITBIT_RESOURCE_BATTERY
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        api: FitbitApi,
        profile: FitbitProfile,
        device: FitbitDevice,
    ) -> None:
        """Initialize the Fitbit sensor."""
        self._api = api
        self._device: FitbitDevice = device
        self._attr_unique_id = (
            f"{profile.encoded_id}_{self.entity_description.key}_{device.id}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=device.device_version,  # Product name
            manufacturer=DEVICE_MANUFACTURER,
            model=device.type,
        )
        self._attr_native_value = device.battery_level

    async def async_update(self) -> None:
        """Get the latest data from the Fitbit API and update the states."""
        devices = await self._api.async_get_devices()
        for device in devices:
            if device.id == self._device.id:
                self._device = device
                self._attr_native_value = device.battery_level
                return
        raise HomeAssistantError(
            f"Device '{self._device.id}' missing from Fitbit API response"
        )


# @property
# def extra_state_attributes(self) -> dict[str, str | None]:
#     """Return the state attributes."""
#     attrs: dict[str, str | None] = {}

#     if self.extra is not None:
#         attrs["model"] = self.extra.get("deviceVersion")
#         extra_type = self.extra.get("type")
#         attrs["type"] = extra_type.lower() if extra_type is not None else None

#     return attrs
