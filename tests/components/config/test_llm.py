"""Tests for the config LLM tools."""

from collections.abc import Iterable
from typing import Any, override
from unittest.mock import patch

import probatio
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant import config_entries, data_entry_flow
from homeassistant.components import config as config_component
from homeassistant.components.config.llm import (
    ConfigureFlowTool,
    GetConfigEntriesTool,
    GetFlowsInProgressTool,
    ReloadConfigEntryTool,
    SearchIntegrationsTool,
    StartFlowTool,
    async_get_tools,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryDisabler,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import llm, selector
from homeassistant.setup import async_setup_component

from tests.common import (
    MockConfigEntry,
    MockModule,
    MockUser,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

TEST_DOMAIN = "test_llm_flow"


@pytest.fixture
def llm_context() -> llm.LLMContext:
    """Return an LLM context."""
    return llm.LLMContext(
        platform="test",
        context=None,
        language="en",
        assistant="conversation",
        device_id=None,
    )


async def test_async_get_tools(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> None:
    """Test async_get_tools returns tools only for Home Assistant API."""
    assert async_get_tools(hass, llm_context, llm.LLM_API_ASSIST) is None

    ha_tools = async_get_tools(hass, llm_context, llm.LLM_API_HOME_ASSISTANT)
    assert ha_tools is not None
    assert [tool.name for tool in ha_tools.tools] == [
        "config__get_config_entries",
        "config__reload_config_entry",
        "config__get_flows_in_progress",
        "config__start_flow",
        "config__configure_flow",
        "config__search_integrations",
    ]
    assert ha_tools.prompt is None


def test_parameters_openapi(snapshot: SnapshotAssertion) -> None:
    """Test that tool schemas can be exported for LLM clients."""
    tools = [
        GetConfigEntriesTool(),
        ReloadConfigEntryTool(),
        GetFlowsInProgressTool(),
        StartFlowTool(),
        ConfigureFlowTool(),
        SearchIntegrationsTool(),
    ]
    assert {
        tool.name: probatio.to_openapi(tool.parameters) for tool in tools
    } == snapshot


async def test_call_through_api(
    hass: HomeAssistant, hass_admin_user: MockUser, llm_context: llm.LLMContext
) -> None:
    """Test tool discovery and execution through the Home Assistant API."""
    mock_integration(hass, MockModule(TEST_DOMAIN))
    assert await async_setup_component(hass, config_component.DOMAIN, {})
    assert await async_setup_component(hass, "llm", {})
    llm_context.context = Context(user_id=hass_admin_user.id)

    entry = MockConfigEntry(
        domain=TEST_DOMAIN,
        title="Philips Hue Bridge",
        entry_id="hue_entry_1",
        state=ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)

    api = await llm.async_get_api(hass, llm.LLM_API_HOME_ASSISTANT, llm_context)
    tool = next(tool for tool in api.tools if tool.name == "config__get_config_entries")
    assert tool.integration == config_component.DOMAIN
    assert tool.annotations == llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )

    result = await api.async_call_tool(
        llm.ToolInput(tool.name, {"domain": TEST_DOMAIN})
    )
    assert result == llm.ToolResult(
        data={
            "result": [
                {
                    "entry_id": "hue_entry_1",
                    "domain": TEST_DOMAIN,
                    "title": "Philips Hue Bridge",
                    "state": "loaded",
                }
            ]
        }
    )


async def test_get_config_entries_filtering_and_capabilities(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> None:
    """Test listing and filtering config entries and formatting capabilities."""
    mock_integration(
        hass, MockModule(TEST_DOMAIN, async_unload_entry=lambda hass, entry: True)
    )
    mock_platform(hass, f"{TEST_DOMAIN}.config_flow", None)

    class TestSubentryFlow(ConfigSubentryFlow):
        """Test subentry flow."""

    class TestFlow(ConfigFlow):
        """Test config flow with options, reconfigure, and subentries."""

        VERSION = 1

        @staticmethod
        @callback
        @override
        def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
            raise NotImplementedError

        @classmethod
        @callback
        @override
        def async_get_supported_subentry_types(
            cls, config_entry: ConfigEntry
        ) -> dict[str, type[ConfigSubentryFlow]]:
            return {"location": TestSubentryFlow}

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            raise NotImplementedError

    with mock_config_flow(TEST_DOMAIN, TestFlow):
        loaded_entry = MockConfigEntry(
            domain=TEST_DOMAIN,
            title="Main Entry",
            entry_id="entry_loaded",
            state=ConfigEntryState.NOT_LOADED,
            subentries_data=[
                {
                    "data": {},
                    "subentry_id": "sub_1",
                    "subentry_type": "location",
                    "title": "Home Location",
                    "unique_id": None,
                }
            ],
        )
        loaded_entry.add_to_hass(hass)

        error_entry = MockConfigEntry(
            domain="zwave_js",
            title="Z-Wave Stick",
            entry_id="entry_error",
            state=ConfigEntryState.SETUP_ERROR,
            reason="USB device disconnected",
        )
        error_entry.add_to_hass(hass)

        translated_error_entry = MockConfigEntry(
            domain="hue",
            title="Hue Bridge",
            entry_id="entry_translated_error",
        )
        translated_error_entry.add_to_hass(hass)
        translated_error_entry._async_set_state(
            hass,
            ConfigEntryState.SETUP_RETRY,
            None,
            "cannot_connect",
            {"host": "192.168.1.50"},
            "hue",
        )

        disabled_entry = MockConfigEntry(
            domain="spotify",
            title="Spotify",
            entry_id="entry_disabled",
            state=ConfigEntryState.NOT_LOADED,
            disabled_by=ConfigEntryDisabler.USER,
        )
        disabled_entry.add_to_hass(hass)

        ignored_entry = MockConfigEntry(
            domain="hue",
            title="Ignored",
            entry_id="entry_ignored",
            source=config_entries.SOURCE_IGNORE,
        )
        ignored_entry.add_to_hass(hass)

        tool = GetConfigEntriesTool()

        async def _mock_get_translations(
            hass_obj: HomeAssistant,
            language: str,
            category: str,
            integrations: Iterable[str] | None = None,
            config_flow: bool | None = None,
        ) -> dict[str, str]:
            if category == "exceptions":
                return {
                    "component.hue.exceptions.cannot_connect.message": (
                        "Failed to connect to bridge at {host}"
                    )
                }
            return {}

        with patch(
            "homeassistant.components.config.llm.translation.async_get_translations",
            side_effect=_mock_get_translations,
        ):
            # Default: returns all non-ignored, non-disabled entries
            all_res = await tool.async_call(
                hass, llm.ToolInput(tool.name, {}), llm_context
            )
            assert all_res == llm.ToolResult(
                data={
                    "result": [
                        {
                            "entry_id": "entry_loaded",
                            "domain": TEST_DOMAIN,
                            "title": "Main Entry",
                            "state": "not_loaded",
                            "supports_options": True,
                            "supports_reconfigure": True,
                            "supported_subentry_types": {
                                "location": {"supports_reconfigure": False}
                            },
                            "subentries": [
                                {
                                    "subentry_id": "sub_1",
                                    "subentry_type": "location",
                                    "title": "Home Location",
                                }
                            ],
                        },
                        {
                            "entry_id": "entry_error",
                            "domain": "zwave_js",
                            "title": "Z-Wave Stick",
                            "state": "setup_error",
                            "reason": "USB device disconnected",
                        },
                        {
                            "entry_id": "entry_translated_error",
                            "domain": "hue",
                            "title": "Hue Bridge",
                            "state": "setup_retry",
                            "reason": "Failed to connect to bridge at 192.168.1.50",
                        },
                    ]
                }
            )

        # Filter by domain list and status
        filtered_res = await tool.async_call(
            hass,
            llm.ToolInput(
                tool.name,
                {"domain": [TEST_DOMAIN, "zwave_js"], "status": "error"},
            ),
            llm_context,
        )
        assert filtered_res == llm.ToolResult(
            data={
                "result": [
                    {
                        "entry_id": "entry_error",
                        "domain": "zwave_js",
                        "title": "Z-Wave Stick",
                        "state": "setup_error",
                        "reason": "USB device disconnected",
                    }
                ]
            }
        )


async def test_reload_config_entry(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> None:
    """Test reloading a config entry and handling errors."""
    tool = ReloadConfigEntryTool()

    # Unknown entry
    not_found = await tool.async_call(
        hass, llm.ToolInput(tool.name, {"entry_id": "missing"}), llm_context
    )
    assert not_found == llm.ToolResult(
        data={"error": "Config entry 'missing' not found."}, error=True
    )

    entry = MockConfigEntry(
        domain=TEST_DOMAIN,
        title="Test Entry",
        entry_id="entry_1",
        state=ConfigEntryState.NOT_LOADED,
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as mock_reload:
        res = await tool.async_call(
            hass, llm.ToolInput(tool.name, {"entry_id": "entry_1"}), llm_context
        )
        mock_reload.assert_awaited_once_with("entry_1")
        assert res == llm.ToolResult(
            data={
                "result": {
                    "entry_id": "entry_1",
                    "state": "not_loaded",
                    "require_restart": False,
                }
            }
        )

    with patch.object(
        hass.config_entries,
        "async_reload",
        side_effect=config_entries.OperationNotAllowed("Entry cannot be reloaded"),
    ):
        err_res = await tool.async_call(
            hass, llm.ToolInput(tool.name, {"entry_id": "entry_1"}), llm_context
        )
        assert err_res.error is True
        assert isinstance(err_msg := err_res.data.get("error"), str)
        assert "Config entry cannot be reloaded" in err_msg


async def test_search_integrations(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> None:
    """Test searching available integrations, helpers, and virtual integrations."""
    tool = SearchIntegrationsTool()

    # Search for a helper integration ("random")
    helper_res = await tool.async_call(
        hass,
        llm.ToolInput(tool.name, {"query": "random", "type": "helper"}),
        llm_context,
    )
    assert helper_res.error is False
    assert isinstance(helper_items := helper_res.data["result"], list)
    assert helper_items[0] == {
        "domain": "random",
        "name": "Random",
        "config_flow": True,
    }

    # Search for a virtual integration supported by another domain ("3_day_blinds")
    virtual_res = await tool.async_call(
        hass,
        llm.ToolInput(tool.name, {"query": "3 Day Blinds", "type": "integration"}),
        llm_context,
    )
    assert virtual_res.error is False
    assert isinstance(virtual_items := virtual_res.data["result"], list)
    assert virtual_items[0] == {
        "domain": "3_day_blinds",
        "name": "3 Day Blinds",
        "config_flow": True,
        "supported_by": "motion_blinds",
    }

    # Multi-word search ("time of day" -> "Times of the Day" / "tod")
    tod_res = await tool.async_call(
        hass,
        llm.ToolInput(tool.name, {"query": "time of day"}),
        llm_context,
    )
    assert tod_res.error is False
    assert isinstance(tod_items := tod_res.data["result"], list)
    assert any(
        isinstance(item, dict) and item.get("domain") == "tod" for item in tod_items
    )

    # Brand with both sub-integrations and iot_standards ("leviton" and "tplink")
    leviton_res = await tool.async_call(
        hass,
        llm.ToolInput(tool.name, {"query": "leviton"}),
        llm_context,
    )
    assert leviton_res.error is False
    assert isinstance(leviton_items := leviton_res.data["result"], list)
    leviton_by_domain = {
        item["domain"]: item for item in leviton_items if isinstance(item, dict)
    }
    assert "decora_wifi" in leviton_by_domain
    assert leviton_by_domain["leviton"] == {
        "domain": "leviton",
        "name": "Leviton",
        "config_flow": False,
        "iot_standards": ["zwave"],
    }

    tplink_res = await tool.async_call(
        hass,
        llm.ToolInput(tool.name, {"query": "tplink"}),
        llm_context,
    )
    assert tplink_res.error is False
    assert isinstance(tplink_items := tplink_res.data["result"], list)
    assert isinstance(tplink_first := tplink_items[0], dict)
    assert tplink_first["domain"] == "tplink"
    assert tplink_first["config_flow"] is True
    assert tplink_first["iot_standards"] == ["matter"]

    # Hardware integrations are excluded from search results
    hw_res = await tool.async_call(
        hass,
        llm.ToolInput(tool.name, {"query": "homeassistant_yellow"}),
        llm_context,
    )
    assert hw_res == llm.ToolResult(data={"result": []})


@pytest.mark.usefixtures("ignore_missing_translations")
async def test_get_flows_in_progress(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
) -> None:
    """Test listing pending discovery and reauth flows."""
    mock_integration(hass, MockModule(TEST_DOMAIN))
    mock_platform(hass, f"{TEST_DOMAIN}.config_flow", None)

    class DiscoveryFlow(ConfigFlow):
        """Test flow supporting zeroconf and user."""

        VERSION = 1

        @override
        async def async_step_zeroconf(
            self, discovery_info: Any = None
        ) -> ConfigFlowResult:
            self.context["title_placeholders"] = {"name": "Living Room Bridge"}
            return await self.async_step_confirm()

        async def async_step_confirm(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            return self.async_show_form(step_id="confirm")

        @override
        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            return self.async_show_form(step_id="user")

    with mock_config_flow(TEST_DOMAIN, DiscoveryFlow):
        # Start a user flow (should be excluded) and a zeroconf flow (should be included)
        await hass.config_entries.flow.async_init(
            TEST_DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        disc_result = await hass.config_entries.flow.async_init(
            TEST_DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}
        )

        tool = GetFlowsInProgressTool()
        res = await tool.async_call(
            hass,
            llm.ToolInput(tool.name, {"domain": TEST_DOMAIN}),
            llm_context,
        )
        assert res == llm.ToolResult(
            data={
                "result": [
                    {
                        "flow_id": disc_result["flow_id"],
                        "domain": TEST_DOMAIN,
                        "source": "zeroconf",
                        "title": "Living Room Bridge",
                        "step_id": "confirm",
                    }
                ]
            }
        )


async def test_start_and_configure_multi_step_config_flow(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> None:
    """Test driving a real built-in multi-step MENU -> FORM -> CREATE_ENTRY config flow (random)."""
    start_tool = StartFlowTool()
    configure_tool = ConfigureFlowTool()

    # 1. Start the 'random' config flow (starts at a MENU step)
    step1 = await start_tool.async_call(
        hass,
        llm.ToolInput(start_tool.name, {"flow_type": "config", "domain": "random"}),
        llm_context,
    )
    assert step1.error is False
    assert isinstance(step1_res := step1.data["result"], dict)
    assert isinstance(flow_id := step1_res["flow_id"], str)
    assert step1_res["type"] == "menu"
    assert step1_res["step_id"] == "user"
    assert isinstance(menu_opts := step1_res["menu_options"], dict)
    assert "sensor" in menu_opts

    # 2. Inspect current step without submitting user_input or next_step_id
    inspected = await configure_tool.async_call(
        hass,
        llm.ToolInput(configure_tool.name, {"flow_id": flow_id}),
        llm_context,
    )
    assert inspected.error is False
    assert isinstance(inspected_res := inspected.data["result"], dict)
    assert inspected_res["type"] == "menu"

    # 2b. Test explicit menu vs form parameter validation errors
    both_err = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "user_input": {}, "next_step_id": "sensor"},
        ),
        llm_context,
    )
    assert both_err == llm.ToolResult(
        data={"error": "Cannot specify both 'user_input' and 'next_step_id'."},
        error=True,
    )

    nested_next_err = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "user_input": {"next_step_id": "sensor"}},
        ),
        llm_context,
    )
    assert nested_next_err == llm.ToolResult(
        data={
            "error": (
                "Use the 'next_step_id' parameter instead of 'user_input' "
                "to select a menu option."
            )
        },
        error=True,
    )

    wrong_param_menu = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "user_input": {}},
        ),
        llm_context,
    )
    assert wrong_param_menu == llm.ToolResult(
        data={
            "error": (
                "Current step is a 'menu' step; pass the chosen option in "
                "'next_step_id' instead of 'user_input'."
            )
        },
        error=True,
    )

    invalid_menu_opt = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "next_step_id": "not_an_option"},
        ),
        llm_context,
    )
    assert invalid_menu_opt.error is True
    assert isinstance(invalid_menu_res := invalid_menu_opt.data["result"], dict)
    assert invalid_menu_res["type"] == "menu"
    assert invalid_menu_res["step_id"] == "user"
    assert "fields" not in invalid_menu_res
    assert isinstance(menu_errors := invalid_menu_res["errors"], dict)
    assert "next_step_id" in menu_errors

    # 3. Choose 'sensor' from the menu using next_step_id
    step2 = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "next_step_id": "sensor"},
        ),
        llm_context,
    )
    assert step2.error is False
    assert isinstance(step2_res := step2.data["result"], dict)
    assert step2_res["type"] == "form"
    assert step2_res["step_id"] == "sensor"
    assert isinstance(fields := step2_res["fields"], list)
    field_names = [f["name"] for f in fields if isinstance(f, dict)]
    assert "name" in field_names
    assert "minimum" in field_names
    assert "maximum" in field_names

    # 3b. Passing next_step_id on a form step returns an explicit error
    wrong_param_form = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "next_step_id": "sensor"},
        ),
        llm_context,
    )
    assert wrong_param_form == llm.ToolResult(
        data={
            "error": (
                "Current step is a 'form' step; pass field values in "
                "'user_input' instead of 'next_step_id'."
            )
        },
        error=True,
    )

    # 4. Submit invalid schema input first to test compact InvalidData handling (no repeated fields)
    invalid_step = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": flow_id, "user_input": {"minimum": "not_an_int"}},
        ),
        llm_context,
    )
    assert invalid_step.error is True
    assert isinstance(invalid_res := invalid_step.data["result"], dict)
    assert invalid_res["type"] == "form"
    assert invalid_res["step_id"] == "sensor"
    assert "fields" not in invalid_res
    assert "errors" in invalid_res

    # 5. Submit valid form input to create the config entry
    step3 = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {
                "flow_id": flow_id,
                "user_input": {
                    "name": "Test Random Sensor",
                    "minimum": 10,
                    "maximum": 50,
                },
            },
        ),
        llm_context,
    )
    await hass.async_block_till_done()
    assert step3.error is False
    assert isinstance(step3_res := step3.data["result"], dict)
    assert step3_res["type"] == "create_entry"
    assert step3_res["title"] == "Test Random Sensor"
    assert isinstance(created_entry_id := step3_res["entry_id"], str)
    assert hass.config_entries.async_get_entry(created_entry_id) is not None


@pytest.mark.parametrize("ignore_translations_for_mock_domains", [TEST_DOMAIN])
@pytest.mark.usefixtures("ignore_translations_for_mock_domains")
async def test_options_reconfigure_and_subentry_flows(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
) -> None:
    """Test driving options (with sections), reconfigure, and subentry flows."""
    mock_integration(hass, MockModule(TEST_DOMAIN))
    mock_platform(hass, f"{TEST_DOMAIN}.config_flow", None)

    class TestOptionsFlow(OptionsFlow):
        """Test options flow with an expandable section and suggested value."""

        async def async_step_init(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            if user_input is not None:
                return self.async_create_entry(title="", data=user_input)
            schema = self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required("interval"): int,
                        probatio.Required("advanced"): data_entry_flow.section(
                            probatio.Schema({probatio.Optional("debug"): bool}),
                            {"collapsed": True},
                        ),
                    }
                ),
                {"interval": 30},
            )
            return self.async_show_form(step_id="init", data_schema=schema)

    class TestSubentryFlow(ConfigSubentryFlow):
        """Test subentry flow supporting user add and reconfigure."""

        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            if user_input is not None:
                return self.async_create_entry(
                    title=user_input["zone_name"], data=user_input
                )
            return self.async_show_form(
                step_id="user",
                data_schema=probatio.Schema({probatio.Required("zone_name"): str}),
            )

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            subentry = self._get_reconfigure_subentry()
            if user_input is not None:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=user_input["zone_name"],
                    data=user_input,
                )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=probatio.Schema({probatio.Required("zone_name"): str}),
            )

    class TestConfigFlow(ConfigFlow):
        """Test config flow supporting options, reconfigure, and subentries."""

        VERSION = 1

        @staticmethod
        @callback
        @override
        def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
            return TestOptionsFlow()

        @classmethod
        @callback
        @override
        def async_get_supported_subentry_types(
            cls, config_entry: ConfigEntry
        ) -> dict[str, type[ConfigSubentryFlow]]:
            return {"zone": TestSubentryFlow}

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            entry = self._get_reconfigure_entry()
            if user_input is not None:
                if user_input["host"] == "192.168.1.20":
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=probatio.Schema({probatio.Required("host"): str}),
                        errors={"base": "cannot_connect"},
                    )
                return self.async_update_reload_and_abort(entry, data=user_input)
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=probatio.Schema(
                    {
                        probatio.Required("host"): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=["192.168.1.10", "192.168.1.20"],
                                translation_key="host_choice",
                            )
                        )
                    }
                ),
            )

    with mock_config_flow(TEST_DOMAIN, TestConfigFlow):
        entry = MockConfigEntry(
            domain=TEST_DOMAIN,
            title="Hub",
            entry_id="hub_entry",
            data={"host": "192.168.1.1"},
        )
        entry.add_to_hass(hass)

        start_tool = StartFlowTool()
        configure_tool = ConfigureFlowTool()

        # 1. Options flow with suggested_value and expandable section
        opt_step1 = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name,
                {"flow_type": "options", "entry_id": entry.entry_id},
            ),
            llm_context,
        )
        assert opt_step1.error is False
        assert isinstance(opt_res1 := opt_step1.data["result"], dict)
        assert isinstance(opt_flow_id := opt_res1["flow_id"], str)
        assert isinstance(opt_fields := opt_res1["fields"], list)
        assert isinstance(interval_field := opt_fields[0], dict)
        assert interval_field["name"] == "interval"
        assert interval_field["suggested_value"] == 30
        assert isinstance(section_field := opt_fields[1], dict)
        assert section_field["name"] == "advanced"
        assert section_field["type"] == "expandable"

        opt_step2 = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {
                    "flow_id": opt_flow_id,
                    "user_input": {"interval": 60, "advanced": {"debug": True}},
                },
            ),
            llm_context,
        )
        assert opt_step2.error is False
        assert isinstance(opt_res2 := opt_step2.data["result"], dict)
        assert opt_res2["type"] == "create_entry"
        assert entry.options == {"interval": 60, "advanced": {"debug": True}}

        # 2. Reconfigure flow (test error recovery then success)
        reconf_step1 = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name,
                {"flow_type": "reconfigure", "entry_id": entry.entry_id},
            ),
            llm_context,
        )
        assert reconf_step1.error is False
        assert isinstance(reconf_res1 := reconf_step1.data["result"], dict)
        assert isinstance(reconf_flow_id := reconf_res1["flow_id"], str)

        reconf_err = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {"flow_id": reconf_flow_id, "user_input": {"host": "192.168.1.20"}},
            ),
            llm_context,
        )
        assert reconf_err.error is False
        assert isinstance(reconf_err_res := reconf_err.data["result"], dict)
        assert reconf_err_res["type"] == "form"
        assert reconf_err_res["errors"] == {"base": "cannot_connect"}

        reconf_done = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {"flow_id": reconf_flow_id, "user_input": {"host": "192.168.1.10"}},
            ),
            llm_context,
        )
        assert reconf_done.error is False
        assert isinstance(reconf_done_res := reconf_done.data["result"], dict)
        assert reconf_done_res["type"] == "abort"
        assert reconf_done_res["reason"] == "reconfigure_successful"
        assert entry.data == {"host": "192.168.1.10"}

        # 3. Subentry flow: add subentry then reconfigure it
        sub_step1 = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name,
                {
                    "flow_type": "subentry",
                    "entry_id": entry.entry_id,
                    "subentry_type": "zone",
                },
            ),
            llm_context,
        )
        assert sub_step1.error is False
        assert isinstance(sub_res1 := sub_step1.data["result"], dict)
        assert isinstance(sub_flow_id := sub_res1["flow_id"], str)

        sub_step2 = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {"flow_id": sub_flow_id, "user_input": {"zone_name": "Backyard"}},
            ),
            llm_context,
        )
        assert sub_step2.error is False
        assert isinstance(sub_res2 := sub_step2.data["result"], dict)
        assert sub_res2["type"] == "create_entry"
        assert len(entry.subentries) == 1
        subentry_id = next(iter(entry.subentries))

        # Reconfigure the subentry
        sub_reconf1 = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name,
                {
                    "flow_type": "reconfigure",
                    "entry_id": entry.entry_id,
                    "subentry_id": subentry_id,
                },
            ),
            llm_context,
        )
        assert sub_reconf1.error is False
        assert isinstance(sub_reconf1_res := sub_reconf1.data["result"], dict)
        assert isinstance(sub_reconf_flow_id := sub_reconf1_res["flow_id"], str)

        sub_reconf2 = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {
                    "flow_id": sub_reconf_flow_id,
                    "user_input": {"zone_name": "Front Yard"},
                },
            ),
            llm_context,
        )
        assert sub_reconf2.error is False
        assert isinstance(sub_reconf2_res := sub_reconf2.data["result"], dict)
        assert sub_reconf2_res["type"] == "abort"
        assert sub_reconf2_res["reason"] == "reconfigure_successful"
        assert entry.subentries[subentry_id].title == "Front Yard"


async def test_abort_flow_and_error_cases(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> None:
    """Test aborting a flow and handling start_flow / configure_flow error cases."""
    start_tool = StartFlowTool()
    configure_tool = ConfigureFlowTool()

    # Missing domain for config flow
    err = await start_tool.async_call(
        hass, llm.ToolInput(start_tool.name, {"flow_type": "config"}), llm_context
    )
    assert err == llm.ToolResult(
        data={"error": "'domain' is required when flow_type is 'config'."},
        error=True,
    )

    # Unknown handler
    err_unknown = await start_tool.async_call(
        hass,
        llm.ToolInput(
            start_tool.name, {"flow_type": "config", "domain": "nonexistent_domain"}
        ),
        llm_context,
    )
    assert err_unknown.error is True

    # Missing entry_id for options flow
    err_no_entry = await start_tool.async_call(
        hass, llm.ToolInput(start_tool.name, {"flow_type": "options"}), llm_context
    )
    assert err_no_entry == llm.ToolResult(
        data={"error": "'entry_id' is required when flow_type is 'options'."},
        error=True,
    )

    # Unknown flow_id for configure_flow
    err_unknown_flow = await configure_tool.async_call(
        hass,
        llm.ToolInput(configure_tool.name, {"flow_id": "unknown_flow"}),
        llm_context,
    )
    assert err_unknown_flow == llm.ToolResult(
        data={"error": "Flow 'unknown_flow' not found."},
        error=True,
    )

    # Start a flow and abort it
    step1 = await start_tool.async_call(
        hass,
        llm.ToolInput(start_tool.name, {"flow_type": "config", "domain": "random"}),
        llm_context,
    )
    assert isinstance(step1_res := step1.data["result"], dict)
    assert isinstance(flow_id := step1_res["flow_id"], str)

    aborted = await configure_tool.async_call(
        hass,
        llm.ToolInput(configure_tool.name, {"flow_id": flow_id, "abort": True}),
        llm_context,
    )
    assert aborted == llm.ToolResult(
        data={
            "result": {
                "flow_id": flow_id,
                "type": "abort",
                "reason": "aborted",
            }
        }
    )
    # Flow should no longer exist
    after_abort = await configure_tool.async_call(
        hass,
        llm.ToolInput(configure_tool.name, {"flow_id": flow_id}),
        llm_context,
    )
    assert after_abort.error is True


async def test_bayesian_chained_subentry_and_reconfigure_journey(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
) -> None:
    """Test Bayesian flow with schema error fallback, chained subentry flow, and subentry reconfigure."""
    start_tool = StartFlowTool()
    configure_tool = ConfigureFlowTool()
    entries_tool = GetConfigEntriesTool()

    # 1. Start Bayesian config flow
    start_res = await start_tool.async_call(
        hass,
        llm.ToolInput(start_tool.name, {"flow_type": "config", "domain": "bayesian"}),
        llm_context,
    )
    assert start_res.error is False
    assert isinstance(step1 := start_res.data["result"], dict)
    assert isinstance(flow_id := step1["flow_id"], str)

    # 2. Submit prior=0 to trigger schema InvalidData and translated extreme_prior_error
    err_res = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {
                "flow_id": flow_id,
                "user_input": {
                    "name": "Room Occupied",
                    "probability_threshold": 80,
                    "prior": 0,
                },
            },
        ),
        llm_context,
    )
    assert err_res.error is True
    assert isinstance(err_step := err_res.data["result"], dict)
    assert "fields" not in err_step
    assert isinstance(errors := err_step["errors"], dict)
    assert "0%" in str(errors["prior"])

    # 3. Submit valid config -> creates entry and chains subentry next_flow
    create_res = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {
                "flow_id": flow_id,
                "user_input": {
                    "name": "Room Occupied",
                    "probability_threshold": 80,
                    "prior": 50,
                },
            },
        ),
        llm_context,
    )
    await hass.async_block_till_done()
    assert create_res.error is False
    assert isinstance(created := create_res.data["result"], dict)
    assert created["type"] == "create_entry"
    assert isinstance(entry_id := created["entry_id"], str)
    assert isinstance(next_flow := created["next_flow"], dict)
    assert next_flow["flow_type"] == "config_subentries_flow"
    assert isinstance(sub_flow_id := next_flow["flow_id"], str)

    # 4. Advance chained subentry menu -> "state", then create observation subentry
    menu_step = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {"flow_id": sub_flow_id, "next_step_id": "state"},
        ),
        llm_context,
    )
    assert menu_step.error is False

    sub_created = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {
                "flow_id": sub_flow_id,
                "user_input": {
                    "entity_id": "sun.sun",
                    "to_state": "above_horizon",
                    "prob_given_true": 90,
                    "prob_given_false": 20,
                    "name": "Daylight",
                },
            },
        ),
        llm_context,
    )
    await hass.async_block_till_done()
    assert sub_created.error is False

    # 5. Inspect entry and reconfigure the observation subentry
    entries_res = await entries_tool.async_call(
        hass,
        llm.ToolInput(entries_tool.name, {"domain": "bayesian"}),
        llm_context,
    )
    assert isinstance(entries_list := entries_res.data["result"], list)
    assert isinstance(b_entry := entries_list[0], dict)
    assert isinstance(subentries := b_entry["subentries"], list)
    assert isinstance(subentry_info := subentries[0], dict)
    assert isinstance(subentry_id := subentry_info["subentry_id"], str)

    reconf_start = await start_tool.async_call(
        hass,
        llm.ToolInput(
            start_tool.name,
            {
                "flow_type": "subentry",
                "entry_id": entry_id,
                "subentry_type": "observation",
                "subentry_id": subentry_id,
            },
        ),
        llm_context,
    )
    assert reconf_start.error is False
    assert isinstance(reconf_step := reconf_start.data["result"], dict)
    assert isinstance(reconf_flow_id := reconf_step["flow_id"], str)

    reconf_done = await configure_tool.async_call(
        hass,
        llm.ToolInput(
            configure_tool.name,
            {
                "flow_id": reconf_flow_id,
                "user_input": {
                    "entity_id": "sun.sun",
                    "to_state": "above_horizon",
                    "prob_given_true": 95,
                    "prob_given_false": 15,
                    "name": "Daylight Strong",
                },
            },
        ),
        llm_context,
    )
    await hass.async_block_till_done()
    assert reconf_done.error is False
    assert isinstance(reconf_res := reconf_done.data["result"], dict)
    assert reconf_res["reason"] == "reconfigure_successful"


@pytest.mark.parametrize("ignore_translations_for_mock_domains", [TEST_DOMAIN])
@pytest.mark.usefixtures("ignore_translations_for_mock_domains")
async def test_oauth_and_external_step_flows(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
) -> None:
    """Test external/OAuth flows abort headlessly and return a frontend URL."""
    mock_integration(hass, MockModule(TEST_DOMAIN))
    mock_platform(hass, f"{TEST_DOMAIN}.config_flow", None)

    mode = "external"

    class OAuthTestFlow(ConfigFlow):
        """Test flow simulating OAuth external steps, missing_credentials, and RuntimeError."""

        VERSION = 1

        async def async_step_auth(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            return self.async_external_step(
                step_id="auth", url="https://example.com/oauth/authorize"
            )

        @override
        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            if mode == "missing_credentials":
                return self.async_abort(reason="missing_credentials")
            if mode == "runtime_error":
                raise RuntimeError("No header in request")
            return await self.async_step_auth()

        async def async_step_reauth(
            self, entry_data: dict[str, Any]
        ) -> ConfigFlowResult:
            return self.async_show_form(step_id="reauth_confirm")

        async def async_step_reauth_confirm(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            if user_input is not None:
                if mode == "runtime_error":
                    raise RuntimeError("No header in request")
                return await self.async_step_auth()
            return self.async_show_form(step_id="reauth_confirm")

    with mock_config_flow(TEST_DOMAIN, OAuthTestFlow):
        start_tool = StartFlowTool()
        configure_tool = ConfigureFlowTool()

        # 1. Start config flow that returns EXTERNAL_STEP -> aborted + add URL
        ext_res = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name, {"flow_type": "config", "domain": TEST_DOMAIN}
            ),
            llm_context,
        )
        assert ext_res.error is True
        assert (
            ext_res.data["frontend_url"]
            == f"/config/integrations/dashboard/add?domain={TEST_DOMAIN}"
        )
        assert hass.config_entries.flow.async_progress_by_handler(TEST_DOMAIN) == []

        # 2. Start config flow that aborts with missing_credentials -> error + add URL
        mode = "missing_credentials"
        cred_res = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name, {"flow_type": "config", "domain": TEST_DOMAIN}
            ),
            llm_context,
        )
        assert cred_res.error is True
        assert (
            cred_res.data["frontend_url"]
            == f"/config/integrations/dashboard/add?domain={TEST_DOMAIN}"
        )
        assert hass.config_entries.flow.async_progress_by_handler(TEST_DOMAIN) == []

        # 3. Start config flow that raises RuntimeError("No header in request") -> cleaned up + add URL
        mode = "runtime_error"
        rt_res = await start_tool.async_call(
            hass,
            llm.ToolInput(
                start_tool.name, {"flow_type": "config", "domain": TEST_DOMAIN}
            ),
            llm_context,
        )
        assert rt_res.error is True
        assert (
            rt_res.data["frontend_url"]
            == f"/config/integrations/dashboard/add?domain={TEST_DOMAIN}"
        )
        assert (
            hass.config_entries.flow.async_progress_by_handler(
                TEST_DOMAIN, include_uninitialized=True
            )
            == []
        )

        # 4. Reauth flow advancing to EXTERNAL_STEP -> aborted + integration page URL
        entry = MockConfigEntry(domain=TEST_DOMAIN, entry_id="test_entry")
        entry.add_to_hass(hass)
        mode = "external"
        reauth_init = await entry.start_reauth_flow(hass)
        reauth_conf = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {"flow_id": reauth_init["flow_id"], "user_input": {}},
            ),
            llm_context,
        )
        assert reauth_conf.error is True
        assert (
            reauth_conf.data["frontend_url"]
            == f"/config/integrations/integration/{TEST_DOMAIN}"
        )
        assert hass.config_entries.flow.async_progress_by_handler(TEST_DOMAIN) == []

        # 5. Reauth flow raising RuntimeError during configure -> aborted + integration page URL
        mode = "runtime_error"
        reauth_init2 = await entry.start_reauth_flow(hass)
        reauth_rt = await configure_tool.async_call(
            hass,
            llm.ToolInput(
                configure_tool.name,
                {"flow_id": reauth_init2["flow_id"], "user_input": {}},
            ),
            llm_context,
        )
        assert reauth_rt.error is True
        assert (
            reauth_rt.data["frontend_url"]
            == f"/config/integrations/integration/{TEST_DOMAIN}"
        )
        assert hass.config_entries.flow.async_progress_by_handler(TEST_DOMAIN) == []


@pytest.mark.parametrize(
    ("tool", "tool_args"),
    [
        pytest.param(
            GetConfigEntriesTool(),
            {"state": "invalid_state"},
            id="entries_invalid_state",
        ),
        pytest.param(
            ReloadConfigEntryTool(),
            {},
            id="reload_missing_entry_id",
        ),
        pytest.param(
            ReloadConfigEntryTool(),
            {"entry_id": ""},
            id="reload_empty_entry_id",
        ),
        pytest.param(
            SearchIntegrationsTool(),
            {"query": ""},
            id="search_empty_query",
        ),
        pytest.param(
            SearchIntegrationsTool(),
            {"query": "hue", "type": "unknown"},
            id="search_invalid_type",
        ),
        pytest.param(
            StartFlowTool(),
            {"flow_type": "invalid"},
            id="start_flow_invalid_type",
        ),
        pytest.param(
            ConfigureFlowTool(),
            {"flow_id": ""},
            id="configure_flow_empty_id",
        ),
    ],
)
async def test_invalid_parameters(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    tool: llm.Tool,
    tool_args: dict[str, Any],
) -> None:
    """Test invalid arguments are rejected by the tool parameter schemas."""
    with pytest.raises(probatio.Invalid):
        await tool.async_call(hass, llm.ToolInput(tool.name, tool_args), llm_context)
