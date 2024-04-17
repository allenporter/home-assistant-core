"""Config flow for the manual integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_ARMING_TIME,
    CONF_CODE,
    CONF_DELAY_TIME,
    CONF_DISARM_AFTER_TRIGGER,
    CONF_NAME,
    CONF_TRIGGER_TIME,
    UnitOfTime,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CODE_ARM_REQUIRED,
    DEFAULT_ARMING_TIME,
    DEFAULT_DELAY_TIME,
    DEFAULT_TRIGGER_TIME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SCHEMA = {
    vol.Optional(CONF_CODE, default=""): cv.string,
    vol.Required(CONF_CODE_ARM_REQUIRED, default="arm_and_disarm"): SelectSelector(
        SelectSelectorConfig(
            options=["arm_and_disarm", "disarm_only"],
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="code_arm_required",
        )
    ),
    vol.Optional(
        CONF_DELAY_TIME, default=DEFAULT_DELAY_TIME.total_seconds()
    ): NumberSelector(
        NumberSelectorConfig(
            min=0, unit_of_measurement=UnitOfTime.SECONDS, mode=NumberSelectorMode.BOX
        ),
    ),
    vol.Optional(
        CONF_ARMING_TIME, default=DEFAULT_ARMING_TIME.total_seconds()
    ): NumberSelector(
        NumberSelectorConfig(
            min=0, unit_of_measurement=UnitOfTime.SECONDS, mode=NumberSelectorMode.BOX
        ),
    ),
    vol.Optional(
        CONF_TRIGGER_TIME, default=DEFAULT_TRIGGER_TIME.total_seconds()
    ): NumberSelector(
        NumberSelectorConfig(
            min=0, unit_of_measurement=UnitOfTime.SECONDS, mode=NumberSelectorMode.BOX
        ),
    ),
    vol.Optional(CONF_DISARM_AFTER_TRIGGER, default=False): cv.boolean,
}


class ManualConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuration for for the manual integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step in the config flow."""

        if user_input is not None:
            user_input[CONF_CODE] = user_input[CONF_CODE].strip()
            user_input[CONF_CODE_ARM_REQUIRED] = (
                user_input[CONF_CODE_ARM_REQUIRED] == "arm_and_disarm"
            )
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    **SCHEMA,
                }
            ),
        )

    # async def async_step_import(
    #     self, import_config: dict[str, Any]
    # ) -> ConfigFlowResult:
    #     """Attempt to import the existing configuration."""
    #     if self._async_current_entries():
    #         return self.async_abort(reason="single_instance_allowed")
    #     main_repeater = Lutron(
    #         import_config[CONF_HOST],
    #         import_config[CONF_USERNAME],
    #         import_config[CONF_PASSWORD],
    #     )

    #     def _load_db() -> None:
    #         main_repeater.load_xml_db()

    #     try:
    #         await self.hass.async_add_executor_job(_load_db)
    #     except HTTPError:
    #         _LOGGER.exception("Http error")
    #         return self.async_abort(reason="cannot_connect")
    #     except Exception:  # pylint: disable=broad-except
    #         _LOGGER.exception("Unknown error")
    #         return self.async_abort(reason="unknown")

    #     guid = main_repeater.guid

    #     if len(guid) <= 10:
    #         return self.async_abort(reason="cannot_connect")
    #     _LOGGER.debug("Main Repeater GUID: %s", main_repeater.guid)

    #     await self.async_set_unique_id(guid)
    #     self._abort_if_unique_id_configured()
    #     return self.async_create_entry(title="Lutron", data=import_config)
