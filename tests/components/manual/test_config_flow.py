"""Tests for the manual config_flow."""

from unittest.mock import patch

from homeassistant.components.manual import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

CODE = "HELLO_CODE"


async def test_default_fields(hass: HomeAssistant) -> None:
    """Test config flow with using mostly default fields."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("step_id") == "user"

    with patch("homeassistant.components.manual.async_setup_entry") as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "test",
            },
        )

        assert result.get("type") is FlowResultType.CREATE_ENTRY
        assert result.get("result").title == "test"
        assert result.get("data") == {
            "name": "test",
            "code": "",
            "code_arm_required": True,
            "arming_time": 60,
            "delay_time": 60,
            "trigger_time": 120,
            "disarm_after_trigger": False,
        }
        assert len(mock_setup.mock_calls) == 1


async def test_config_flow(hass: HomeAssistant) -> None:
    """Test completing config flow and changing fields from default values."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("step_id") == "user"

    with patch("homeassistant.components.manual.async_setup_entry") as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "test",
                "code": CODE,
                "code_arm_required": "disarm_only",
                "arming_time": 30,
                "delay_time": 45,
                "trigger_time": 300,
                "disarm_after_trigger": True,
            },
        )

        assert result.get("type") is FlowResultType.CREATE_ENTRY
        assert result.get("result").title == "test"
        assert result.get("data") == {
            "name": "test",
            "code": CODE,
            "code_arm_required": False,
            "arming_time": 30,
            "delay_time": 45,
            "trigger_time": 300,
            "disarm_after_trigger": True,
        }
        assert len(mock_setup.mock_calls) == 1


async def test_code_strips_whitespace(hass: HomeAssistant) -> None:
    """Test completing config flow and changing fields from default values."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("step_id") == "user"

    with patch("homeassistant.components.manual.async_setup_entry") as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "test",
                "code": " 1234 ",
                "code_arm_required": "arm_and_disarm",
            },
        )

        assert result.get("type") is FlowResultType.CREATE_ENTRY
        assert result.get("result").title == "test"
        assert result.get("data") == {
            "name": "test",
            "code": "1234",
            "code_arm_required": True,
            "arming_time": 60,
            "delay_time": 60,
            "trigger_time": 120,
            "disarm_after_trigger": False,
        }
        assert len(mock_setup.mock_calls) == 1
