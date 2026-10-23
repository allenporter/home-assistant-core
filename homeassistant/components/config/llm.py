"""LLM tools for the config integration."""

from collections.abc import Iterable, Iterator, Mapping
from contextlib import suppress
import heapq
import re
from typing import Any, Literal, override

import probatio

from homeassistant import config_entries, data_entry_flow
from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import DependencyError, HomeAssistantError
from homeassistant.helpers import config_validation as cv, translation
from homeassistant.helpers.llm import (
    LLM_API_HOME_ASSISTANT,
    LLMContext,
    Tool,
    ToolAnnotations,
    ToolInput,
    ToolResult,
)
from homeassistant.loader import async_get_integration_descriptions
from homeassistant.util.json import JsonObjectType, JsonValueType

from .const import DOMAIN

_SEARCH_LIMIT = 15
_STATUS_FILTERS: dict[str, set[ConfigEntryState]] = {
    "loaded": {ConfigEntryState.LOADED},
    "error": {
        ConfigEntryState.SETUP_ERROR,
        ConfigEntryState.SETUP_RETRY,
        ConfigEntryState.MIGRATION_ERROR,
        ConfigEntryState.FAILED_UNLOAD,
    },
    "not_loaded": {ConfigEntryState.NOT_LOADED},
}
_FLOW_TYPES = ["config", "options", "reconfigure", "subentry"]
_INTEGRATION_FILTER_TYPES = ["integration", "helper"]
_OAUTH_RUNTIME_ERRORS = {"No header in request", "No current request in context"}

type FlowCategory = Literal["config", "options", "config_subentries"]
type _SearchableIntegration = tuple[
    str, str, tuple[str, ...], tuple[str, ...], JsonObjectType
]


def _resolve_entry_reason(
    entry: ConfigEntry,
    exc_trans: dict[str, str],
    config_trans: dict[str, str],
) -> str | None:
    """Resolve a human-readable error reason for a ConfigEntry."""
    if key := entry.error_reason_translation_key:
        trans_domain = entry.error_reason_translation_domain or entry.domain
        msg = exc_trans.get(f"component.{trans_domain}.exceptions.{key}.message")
        if resolved := _interpolate_placeholders(
            msg, entry.error_reason_translation_placeholders
        ):
            return resolved
        return entry.reason or key
    if (reason := entry.reason) is not None:
        return config_trans.get(
            f"component.{entry.domain}.config.error.{reason}", reason
        )
    return None


async def _async_load_entry_error_translations(
    hass: HomeAssistant,
    entries: Iterable[ConfigEntry],
    language: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Load exception and config error translations for entries with error reasons."""
    exc_domains: set[str] = set()
    config_domains: set[str] = set()
    for entry in entries:
        if entry.error_reason_translation_key:
            exc_domains.add(entry.error_reason_translation_domain or entry.domain)
        elif entry.reason is not None:
            config_domains.add(entry.domain)

    if not exc_domains and not config_domains:
        return {}, {}

    lang = language if language and language != "*" else (hass.config.language or "en")
    exc_trans = (
        await translation.async_get_translations(hass, lang, "exceptions", exc_domains)
        if exc_domains
        else {}
    )
    config_trans = (
        await translation.async_get_translations(hass, lang, "config", config_domains)
        if config_domains
        else {}
    )
    return exc_trans, config_trans


_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _format_config_entry(
    entry: ConfigEntry,
    exc_trans: dict[str, str] | None = None,
    config_trans: dict[str, str] | None = None,
) -> JsonObjectType:
    """Format a ConfigEntry into a compact dictionary for LLM consumption."""
    result: JsonObjectType = {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "state": entry.state.value,
    }
    if (
        reason := _resolve_entry_reason(entry, exc_trans or {}, config_trans or {})
    ) is not None:
        result["reason"] = reason
    if entry.supports_options:
        result["supports_options"] = True
    if entry.supports_reconfigure:
        result["supports_reconfigure"] = True
    if entry.supported_subentry_types:
        result["supported_subentry_types"] = {
            sub_type: dict[str, JsonValueType](sub_meta)
            for sub_type, sub_meta in entry.supported_subentry_types.items()
        }
    if entry.subentries:
        result["subentries"] = [
            {
                "subentry_id": subentry.subentry_id,
                "subentry_type": subentry.subentry_type,
                "title": subentry.title,
            }
            for subentry in entry.subentries.values()
        ]
    return result


def _interpolate_placeholders(
    text: str | None, placeholders: Mapping[str, Any] | None
) -> str | None:
    """Replace {placeholder} tokens in a translation string."""
    if not text or not placeholders:
        return text
    return _PLACEHOLDER_RE.sub(
        lambda m: (
            str(placeholders[key])
            if (key := m.group(1)) in placeholders
            else m.group(0)
        ),
        text,
    )


def _enrich_select_options(
    options: list[Any],
    selector_trans: dict[str, str],
    domain: str,
    trans_domain: str,
    sel_trans_key: str,
) -> list[Any]:
    """Return a new options list with translated labels without mutating the source."""
    new_options: list[Any] = []
    for opt in options:
        if isinstance(opt, str):
            opt_label = selector_trans.get(
                f"component.{trans_domain}.selector.{sel_trans_key}.options.{opt}"
            ) or selector_trans.get(
                f"component.{domain}.selector.{sel_trans_key}.options.{opt}"
            )
            new_options.append({"value": opt, "label": opt_label} if opt_label else opt)
        elif isinstance(opt, dict) and isinstance(opt_val := opt.get("value"), str):
            opt_label = (
                selector_trans.get(
                    f"component.{trans_domain}.selector.{sel_trans_key}.options.{opt_val}"
                )
                or selector_trans.get(
                    f"component.{domain}.selector.{sel_trans_key}.options.{opt_val}"
                )
                or opt.get("label")
            )
            new_options.append({**opt, "label": opt_label} if opt_label else dict(opt))
        else:
            new_options.append(opt)
    return new_options


def _enrich_schema_fields(
    fields: list[dict[str, Any]],
    trans: dict[str, str],
    selector_trans: dict[str, str],
    domain: str,
    trans_domain: str,
    prefix: str,
    trans_prefix: str,
    placeholders: Mapping[str, Any] | None,
) -> list[JsonValueType]:
    """Enrich probatio.to_field_list items with resolved translations."""
    enriched: list[JsonValueType] = []
    for raw_field in fields:
        field: dict[str, Any] = dict(raw_field)
        name: str = field["name"]

        if isinstance(desc_meta := field.get("description"), dict):
            field.pop("description")
            if "suggested_value" in desc_meta:
                field["suggested_value"] = desc_meta["suggested_value"]

        if field.get("type") == "expandable" and isinstance(
            nested_schema := field.get("schema"), list
        ):
            sec_prefix = f"{prefix}.sections.{name}"
            trans_sec_prefix = f"{trans_prefix}.sections.{name}"
            if sec_name := _interpolate_placeholders(
                trans.get(f"{trans_sec_prefix}.name")
                or trans.get(f"{sec_prefix}.name"),
                placeholders,
            ):
                field["label"] = sec_name
            if sec_desc := _interpolate_placeholders(
                trans.get(f"{trans_sec_prefix}.description")
                or trans.get(f"{sec_prefix}.description"),
                placeholders,
            ):
                field["description"] = sec_desc
            field["schema"] = _enrich_schema_fields(
                nested_schema,
                trans,
                selector_trans,
                domain,
                trans_domain,
                sec_prefix,
                trans_sec_prefix,
                placeholders,
            )
            enriched.append(field)
            continue

        if label := _interpolate_placeholders(
            trans.get(f"{trans_prefix}.data.{name}")
            or trans.get(f"{prefix}.data.{name}"),
            placeholders,
        ):
            field["label"] = label
        if description := _interpolate_placeholders(
            trans.get(f"{trans_prefix}.data_description.{name}")
            or trans.get(f"{prefix}.data_description.{name}"),
            placeholders,
        ):
            field["description"] = description

        if (
            isinstance(selector_cfg := field.get("selector"), dict)
            and isinstance(select_cfg := selector_cfg.get("select"), dict)
            and isinstance(options := select_cfg.get("options"), list)
        ):
            sel_trans_key = select_cfg.get("translation_key") or name
            field["selector"] = {
                **selector_cfg,
                "select": {
                    **select_cfg,
                    "options": _enrich_select_options(
                        options, selector_trans, domain, trans_domain, sel_trans_key
                    ),
                },
            }

        enriched.append(field)
    return enriched


def _resolve_step_title_and_description(
    formatted: JsonObjectType,
    trans: dict[str, str],
    title_trans: dict[str, str],
    domain: str,
    step_prefix: str,
    trans_step_prefix: str,
    placeholders: Mapping[str, Any] | None,
) -> None:
    """Populate step title and description on a formatted flow step."""
    title = (
        trans.get(f"{trans_step_prefix}.title")
        or trans.get(f"{step_prefix}.title")
        or title_trans.get(f"component.{domain}.title")
    )
    if title := _interpolate_placeholders(title, placeholders):
        formatted["title"] = title

    description = trans.get(f"{trans_step_prefix}.description") or trans.get(
        f"{step_prefix}.description"
    )
    if description := _interpolate_placeholders(description, placeholders):
        formatted["description"] = description


def _translate_error_value(
    err_val: Any,
    trans: dict[str, str],
    config_trans: dict[str, str],
    base_prefix: str,
    domain_prefix: str,
    trans_domain: str,
    placeholders: Mapping[str, Any] | None,
) -> Any:
    """Translate an error key or list of error keys if translations exist."""
    if isinstance(err_val, str):
        err_msg = (
            trans.get(f"{base_prefix}.error.{err_val}")
            or trans.get(f"{domain_prefix}.error.{err_val}")
            or config_trans.get(f"component.{trans_domain}.config.error.{err_val}")
            or err_val
        )
        return _interpolate_placeholders(err_msg, placeholders) or err_val
    if isinstance(err_val, list):
        return [
            _translate_error_value(
                item,
                trans,
                config_trans,
                base_prefix,
                domain_prefix,
                trans_domain,
                placeholders,
            )
            for item in err_val
        ]
    return err_val


def _format_form_step(
    formatted: JsonObjectType,
    result: data_entry_flow.FlowResult[Any, Any],
    trans: dict[str, str],
    config_trans: dict[str, str],
    title_trans: dict[str, str],
    selector_trans: dict[str, str],
    domain: str,
    trans_domain: str,
    base_prefix: str,
    domain_prefix: str,
    placeholders: Mapping[str, Any] | None,
) -> None:
    """Populate fields and errors for a FORM flow result."""
    step_id = result["step_id"]
    formatted["step_id"] = step_id
    step_prefix = f"{domain_prefix}.step.{step_id}"
    trans_step_prefix = f"{base_prefix}.step.{step_id}"

    _resolve_step_title_and_description(
        formatted,
        trans,
        title_trans,
        domain,
        step_prefix,
        trans_step_prefix,
        placeholders,
    )
    if (last_step := result.get("last_step")) is not None:
        formatted["last_step"] = last_step

    schema = result.get("data_schema")
    serialized = (
        probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)
        if schema is not None
        else []
    )
    raw_fields = serialized if isinstance(serialized, list) else []
    formatted["fields"] = _enrich_schema_fields(
        raw_fields,
        trans,
        selector_trans,
        domain,
        trans_domain,
        step_prefix,
        trans_step_prefix,
        placeholders,
    )

    if err_source := result.get("errors"):
        formatted["errors"] = {
            err_field: _translate_error_value(
                err_val,
                trans,
                config_trans,
                base_prefix,
                domain_prefix,
                trans_domain,
                placeholders,
            )
            for err_field, err_val in err_source.items()
        }


def _format_menu_step(
    formatted: JsonObjectType,
    result: data_entry_flow.FlowResult[Any, Any],
    trans: dict[str, str],
    title_trans: dict[str, str],
    domain: str,
    base_prefix: str,
    domain_prefix: str,
    placeholders: Mapping[str, Any] | None,
) -> None:
    """Populate menu_options for a MENU flow result."""
    step_id = result["step_id"]
    formatted["step_id"] = step_id
    step_prefix = f"{domain_prefix}.step.{step_id}"
    trans_step_prefix = f"{base_prefix}.step.{step_id}"

    _resolve_step_title_and_description(
        formatted,
        trans,
        title_trans,
        domain,
        step_prefix,
        trans_step_prefix,
        placeholders,
    )

    raw_menu_options = result.get("menu_options", [])
    menu_options_out: JsonObjectType = {}
    option_keys: Iterable[str] = (
        raw_menu_options.keys()
        if isinstance(raw_menu_options, Mapping)
        else raw_menu_options
        if isinstance(raw_menu_options, Iterable)
        else ()
    )

    for opt in option_keys:
        label = (
            trans.get(f"{trans_step_prefix}.menu_options.{opt}")
            or trans.get(f"{step_prefix}.menu_options.{opt}")
            or (raw_menu_options[opt] if isinstance(raw_menu_options, Mapping) else opt)
        )
        opt_desc = trans.get(
            f"{trans_step_prefix}.menu_option_descriptions.{opt}"
        ) or trans.get(f"{step_prefix}.menu_option_descriptions.{opt}")
        label_str = _interpolate_placeholders(label, placeholders) or opt
        if opt_desc_str := _interpolate_placeholders(opt_desc, placeholders):
            menu_options_out[opt] = {
                "label": label_str,
                "description": opt_desc_str,
            }
        else:
            menu_options_out[opt] = label_str
    formatted["menu_options"] = menu_options_out


def _format_terminal_or_progress_step(
    formatted: JsonObjectType,
    result: data_entry_flow.FlowResult[Any, Any],
    flow_type: data_entry_flow.FlowResultType | str,
    trans: dict[str, str],
    config_trans: dict[str, str],
    exc_trans: dict[str, str],
    trans_domain: str,
    base_prefix: str,
    domain_prefix: str,
    placeholders: Mapping[str, Any] | None,
) -> None:
    """Populate fields for CREATE_ENTRY, ABORT, or SHOW_PROGRESS."""
    if flow_type is data_entry_flow.FlowResultType.CREATE_ENTRY:
        if (title := result.get("title")) is not None:
            formatted["title"] = title
        desc_key = result.get("description") or "default"
        if desc := _interpolate_placeholders(
            trans.get(f"{base_prefix}.create_entry.{desc_key}")
            or trans.get(f"{domain_prefix}.create_entry.{desc_key}"),
            placeholders,
        ):
            formatted["description"] = desc
        if isinstance(created_entry := result.get("result"), ConfigEntry):
            formatted["entry_id"] = created_entry.entry_id
            formatted["config_entry"] = _format_config_entry(
                created_entry, exc_trans, config_trans
            )
        if (
            isinstance(next_flow := result.get("next_flow"), tuple)
            and len(next_flow) == 2
        ):
            formatted["next_flow"] = {
                "flow_type": str(next_flow[0]),
                "flow_id": str(next_flow[1]),
            }

    elif flow_type is data_entry_flow.FlowResultType.ABORT:
        reason = result["reason"]
        formatted["reason"] = reason
        abort_msg = (
            trans.get(f"{base_prefix}.abort.{reason}")
            or trans.get(f"{domain_prefix}.abort.{reason}")
            or config_trans.get(f"component.{trans_domain}.config.abort.{reason}")
            or config_trans.get(f"component.homeassistant.config.abort.{reason}")
        )
        if abort_text := _interpolate_placeholders(abort_msg, placeholders):
            formatted["description"] = abort_text
        if (
            isinstance(next_flow := result.get("next_flow"), tuple)
            and len(next_flow) == 2
        ):
            formatted["next_flow"] = {
                "flow_type": str(next_flow[0]),
                "flow_id": str(next_flow[1]),
            }

    elif flow_type is data_entry_flow.FlowResultType.SHOW_PROGRESS:
        step_id = result["step_id"]
        progress_action = result["progress_action"]
        formatted["step_id"] = step_id
        formatted["progress_action"] = progress_action
        step_prefix = f"{domain_prefix}.step.{step_id}"
        trans_step_prefix = f"{base_prefix}.step.{step_id}"
        if title := _interpolate_placeholders(
            trans.get(f"{trans_step_prefix}.title")
            or trans.get(f"{step_prefix}.title"),
            placeholders,
        ):
            formatted["title"] = title
        if description := _interpolate_placeholders(
            trans.get(f"{base_prefix}.progress.{progress_action}")
            or trans.get(f"{domain_prefix}.progress.{progress_action}"),
            placeholders,
        ):
            formatted["description"] = description


async def async_format_flow_result(
    hass: HomeAssistant,
    result: data_entry_flow.FlowResult[Any, Any],
    flow_category: FlowCategory,
    domain: str,
    language: str | None,
    *,
    subentry_type: str | None = None,
) -> JsonObjectType:
    """Format a DataEntryFlow result with resolved translations and field metadata."""
    lang = language if language and language != "*" else (hass.config.language or "en")
    trans_domain: str = result.get("translation_domain") or domain
    domains_to_load = {domain, trans_domain, "homeassistant"}

    trans = await translation.async_get_translations(
        hass, lang, flow_category, domains_to_load
    )
    config_trans = (
        trans
        if flow_category == "config"
        else await translation.async_get_translations(
            hass, lang, "config", domains_to_load
        )
    )
    title_trans = await translation.async_get_translations(
        hass, lang, "title", {domain}
    )

    if flow_category == "config_subentries":
        base_prefix = f"component.{trans_domain}.config_subentries.{subentry_type}"
        domain_prefix = f"component.{domain}.config_subentries.{subentry_type}"
    else:
        base_prefix = f"component.{trans_domain}.{flow_category}"
        domain_prefix = f"component.{domain}.{flow_category}"

    placeholders: Mapping[str, Any] | None = result.get("description_placeholders")
    flow_type = result["type"]
    formatted: JsonObjectType = {
        "flow_id": result["flow_id"],
        "type": str(
            flow_type.value
            if isinstance(flow_type, data_entry_flow.FlowResultType)
            else flow_type
        ),
    }

    if flow_type is data_entry_flow.FlowResultType.FORM:
        selector_trans = await translation.async_get_translations(
            hass, lang, "selector", domains_to_load
        )
        _format_form_step(
            formatted,
            result,
            trans,
            config_trans,
            title_trans,
            selector_trans,
            domain,
            trans_domain,
            base_prefix,
            domain_prefix,
            placeholders,
        )
    elif flow_type is data_entry_flow.FlowResultType.MENU:
        _format_menu_step(
            formatted,
            result,
            trans,
            title_trans,
            domain,
            base_prefix,
            domain_prefix,
            placeholders,
        )
    else:
        exc_trans: dict[str, str] = {}
        if isinstance(created_entry := result.get("result"), ConfigEntry):
            exc_trans, _ = await _async_load_entry_error_translations(
                hass, (created_entry,), lang
            )
        _format_terminal_or_progress_step(
            formatted,
            result,
            flow_type,
            trans,
            config_trans,
            exc_trans,
            trans_domain,
            base_prefix,
            domain_prefix,
            placeholders,
        )

    return formatted


async def async_format_schema_errors(
    hass: HomeAssistant,
    flow_id: str,
    step_id: str,
    step_type: str,
    schema_errors: dict[str, Any],
    flow_category: FlowCategory,
    domain: str,
    language: str | None,
    *,
    subentry_type: str | None = None,
    cur_step: data_entry_flow.FlowResult[Any, Any] | None = None,
) -> JsonObjectType:
    """Format schema validation errors for a flow step without re-serializing fields."""
    lang = language if language and language != "*" else (hass.config.language or "en")
    trans_domain: str = (
        cur_step.get("translation_domain") if cur_step else None
    ) or domain
    placeholders: Mapping[str, Any] | None = (
        cur_step.get("description_placeholders") if cur_step else None
    )
    domains_to_load = {domain, trans_domain, "homeassistant"}

    trans = await translation.async_get_translations(
        hass, lang, flow_category, domains_to_load
    )
    config_trans = (
        trans
        if flow_category == "config"
        else await translation.async_get_translations(
            hass, lang, "config", domains_to_load
        )
    )

    if flow_category == "config_subentries":
        base_prefix = f"component.{trans_domain}.config_subentries.{subentry_type}"
        domain_prefix = f"component.{domain}.config_subentries.{subentry_type}"
    else:
        base_prefix = f"component.{trans_domain}.{flow_category}"
        domain_prefix = f"component.{domain}.{flow_category}"

    errors_out: JsonObjectType = {
        err_field: _translate_error_value(
            err_val,
            trans,
            config_trans,
            base_prefix,
            domain_prefix,
            trans_domain,
            placeholders,
        )
        for err_field, err_val in schema_errors.items()
    }
    return {
        "flow_id": flow_id,
        "type": step_type,
        "step_id": step_id,
        "errors": errors_out,
    }


def _rank_integration(
    query: str,
    domain: str,
    name: str,
    brand_names: Iterable[str],
    other: Iterable[str],
) -> int | None:
    """Rank an integration match against a case-folded query string."""
    domain_folded = domain.casefold()
    name_folded = name.casefold()
    if query in {name_folded, domain_folded}:
        return 0
    for brand_name in brand_names:
        if brand_name.casefold() == query:
            return 1
    if query in name_folded or query in domain_folded:
        return 2
    if len(query_words := query.split()) > 1 and all(
        word in name_folded or word in domain_folded for word in query_words
    ):
        return 3
    for item in (*brand_names, *other):
        if query in item.casefold():
            return 4
    return None


def _build_integration_meta_index(
    custom_integrations: dict[str, Any],
    core_integrations: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build an O(1) lookup index mapping domain to integration metadata dict."""
    index: dict[str, dict[str, Any]] = {}
    for domain, item in core_integrations.items():
        if "integration_type" in item:
            index[domain] = item
        elif sub_integrations := item.get("integrations"):
            index.update(sub_integrations)
    for domain, item in custom_integrations.items():
        if "integration_type" in item:
            index[domain] = item
        elif sub_integrations := item.get("integrations"):
            index.update(sub_integrations)
    return index


def _build_searchable_entry(
    domain: str,
    meta: dict[str, Any],
    default_type: str,
    title_trans: dict[str, str],
    meta_index: dict[str, dict[str, Any]],
    brand_name: str | None = None,
    brand_iot_standards: list[str] | None = None,
) -> _SearchableIntegration | None:
    """Build a searchable integration tuple from an integration description dict."""
    integration_type = meta.get("integration_type", default_type)
    if integration_type == "hardware":
        return None

    resolved_meta = meta
    supported_by: str | None = meta.get("supported_by")
    if supported_by and (target_meta := meta_index.get(supported_by)):
        resolved_meta = target_meta

    name: str = (
        meta.get("name") or title_trans.get(f"component.{domain}.title") or domain
    )
    entry_info: JsonObjectType = {
        "domain": domain,
        "name": name,
        "config_flow": bool(resolved_meta.get("config_flow", False)),
    }
    if supported_by:
        entry_info["supported_by"] = supported_by
    iot_standards: list[str] | None = (
        resolved_meta.get("iot_standards") or brand_iot_standards
    )
    if iot_standards:
        entry_info["iot_standards"] = list[JsonValueType](iot_standards)
    if resolved_meta.get("single_config_entry"):
        entry_info["single_config_entry"] = True

    brands = (brand_name,) if brand_name else ()
    other_terms: list[str] = []
    if supported_by:
        other_terms.append(supported_by)
    if categories := meta.get("categories"):
        other_terms.extend(categories)
    if iot_standards:
        other_terms.extend(iot_standards)

    return (domain, name, brands, tuple(other_terms), entry_info)


def _iter_integration_group(
    source_dict: dict[str, Any],
    seen_domains: set[str],
    title_trans: dict[str, str],
    meta_index: dict[str, dict[str, Any]],
) -> Iterator[_SearchableIntegration]:
    """Yield searchable integrations from a single integration dictionary."""
    for key, info in source_dict.items():
        if "integration_type" in info:
            if key not in seen_domains:
                seen_domains.add(key)
                if built := _build_searchable_entry(
                    key,
                    info,
                    "integration",
                    title_trans,
                    meta_index,
                ):
                    yield built
            continue

        brand_name = info.get("name", key)
        iot_standards: list[str] | None = info.get("iot_standards")
        if sub_integrations := info.get("integrations"):
            for sub_domain, sub_info in sub_integrations.items():
                if sub_domain in seen_domains:
                    continue
                seen_domains.add(sub_domain)
                if built := _build_searchable_entry(
                    sub_domain,
                    sub_info,
                    "integration",
                    title_trans,
                    meta_index,
                    brand_name=brand_name,
                    brand_iot_standards=iot_standards if sub_domain == key else None,
                ):
                    yield built
        if iot_standards and key not in seen_domains:
            seen_domains.add(key)
            yield (
                key,
                brand_name,
                (),
                tuple(iot_standards),
                {
                    "domain": key,
                    "name": brand_name,
                    "config_flow": False,
                    "iot_standards": list[JsonValueType](iot_standards),
                },
            )


def _iter_searchable_integrations(
    descriptions: dict[str, Any],
    title_trans: dict[str, str],
    filter_type: str | None,
) -> Iterator[_SearchableIntegration]:
    """Yield searchable integration entries with resolved metadata."""
    core_integrations: dict[str, Any] = descriptions["core"]["integration"]
    custom_integrations: dict[str, Any] = descriptions["custom"]["integration"]
    core_helpers: dict[str, Any] = descriptions["core"]["helper"]
    custom_helpers: dict[str, Any] = descriptions["custom"]["helper"]
    meta_index = _build_integration_meta_index(custom_integrations, core_integrations)

    if filter_type in (None, "integration"):
        seen_domains: set[str] = set()
        for source_dict in (custom_integrations, core_integrations):
            yield from _iter_integration_group(
                source_dict,
                seen_domains,
                title_trans,
                meta_index,
            )

    if filter_type in (None, "helper"):
        seen_helpers: set[str] = set()
        for source_dict in (custom_helpers, core_helpers):
            for domain, info in source_dict.items():
                if domain in seen_helpers:
                    continue
                seen_helpers.add(domain)
                if built := _build_searchable_entry(
                    domain,
                    info,
                    "helper",
                    title_trans,
                    meta_index,
                ):
                    yield built


class GetConfigEntriesTool(Tool):
    """LLM Tool to list and filter config entries."""

    name = "config__get_config_entries"
    title = "Get config entries"
    description = (
        "Retrieve installed Home Assistant config entries (active integrations and "
        "helpers configured in this instance; excludes ignored and disabled entries). "
        "Use this to inspect which integrations are already set up, check their status "
        "('loaded', 'error', 'not_loaded'), see translated error reasons, and find "
        "entry_ids and supported flow capabilities (supports_options, "
        "supports_reconfigure, supported_subentry_types)."
    )
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Optional(
                "domain",
                description=(
                    "Filter by integration domain (e.g. 'hue', 'zwave_js'). "
                    "Accepts a single domain or a list of domains."
                ),
            ): probatio.Any(str, [str]),
            probatio.Optional(
                "status",
                description=(
                    "Filter by status: 'loaded' (working normally), 'error' "
                    "(failed setup, retrying, migration error, or failed unload), "
                    "or 'not_loaded'."
                ),
            ): probatio.All(str, probatio.Lower, probatio.In(list(_STATUS_FILTERS))),
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Return matching config entries."""
        args = self.parameters(tool_input.tool_args)
        domain_filter = args.get("domain")
        status_filter: str | None = args.get("status")

        entries = hass.config_entries.async_entries(
            include_ignore=False, include_disabled=False
        )

        if domain_filter is not None:
            if isinstance(domain_filter, str):
                domains = {domain_filter.strip().lower()}
            else:
                domains = {d.strip().lower() for d in domain_filter if d.strip()}
            entries = [entry for entry in entries if entry.domain.lower() in domains]

        if status_filter is not None:
            allowed_states = _STATUS_FILTERS[status_filter]
            entries = [entry for entry in entries if entry.state in allowed_states]

        exc_trans, config_trans = await _async_load_entry_error_translations(
            hass, entries, llm_context.language
        )

        return ToolResult(
            data={
                "result": list[JsonValueType](
                    _format_config_entry(entry, exc_trans, config_trans)
                    for entry in entries
                )
            }
        )


class ReloadConfigEntryTool(Tool):
    """LLM Tool to reload a config entry."""

    name = "config__reload_config_entry"
    title = "Reload config entry"
    description = (
        "Reload an installed Home Assistant config entry by entry_id. Use this to "
        "recover an integration from a transient setup/connection error or apply changes."
    )
    annotations = ToolAnnotations(
        read_only=False, destructive=False, idempotent=True, open_world=False
    )
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required(
                "entry_id",
                description="The entry_id of the config entry to reload.",
            ): probatio.All(str, probatio.NonEmpty()),
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Reload the specified config entry."""
        args = self.parameters(tool_input.tool_args)
        entry_id: str = args["entry_id"]

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.source == config_entries.SOURCE_IGNORE:
            return ToolResult(
                data={"error": f"Config entry '{entry_id}' not found."},
                error=True,
            )

        try:
            await hass.config_entries.async_reload(entry_id)
        except config_entries.OperationNotAllowed as err:
            return ToolResult(
                data={"error": f"Config entry cannot be reloaded: {err}"},
                error=True,
            )

        exc_trans, config_trans = await _async_load_entry_error_translations(
            hass, (entry,), llm_context.language
        )
        result: JsonObjectType = {
            "entry_id": entry.entry_id,
            "state": entry.state.value,
            "require_restart": not entry.state.recoverable,
        }
        if (
            reason := _resolve_entry_reason(entry, exc_trans, config_trans)
        ) is not None:
            result["reason"] = reason

        return ToolResult(data={"result": result})


class SearchIntegrationsTool(Tool):
    """LLM Tool to search available integrations and helpers."""

    name = "config__search_integrations"
    title = "Search available integrations"
    description = (
        "Search the catalog of all available Home Assistant integrations and helpers "
        "(including uninstalled ones) by name, domain, brand, or IoT standard to find "
        "the domain required to set up a new config entry with config__start_flow "
        "(use config__get_config_entries instead to list integrations that are already "
        "installed). Returns whether the integration supports a UI config_flow, or if "
        "it is a virtual integration supported_by another integration domain or IoT "
        "standard (zigbee, zwave, matter, homekit)."
    )
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required(
                "query",
                description="Search text to match against integration name, domain, brand, or IoT standard (case-insensitive).",
            ): probatio.All(str, probatio.NonEmpty()),
            probatio.Optional(
                "type",
                description="Filter results by type: 'integration' or 'helper'.",
            ): probatio.All(
                str, probatio.Lower, probatio.In(_INTEGRATION_FILTER_TYPES)
            ),
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Search available integrations and helpers."""
        args = self.parameters(tool_input.tool_args)
        query = args["query"].strip().casefold()
        filter_type: str | None = args.get("type")

        descriptions = await async_get_integration_descriptions(hass)
        lang = (
            llm_context.language
            if llm_context.language and llm_context.language != "*"
            else (hass.config.language or "en")
        )
        translated_domains = set(descriptions["core"].get("translated_name", ()))
        translated_domains.update(descriptions["core"].get("helper", {}).keys())
        title_trans = await translation.async_get_translations(
            hass, lang, "title", translated_domains
        )

        def _matches() -> Iterator[tuple[bool, int, str, str, JsonObjectType]]:
            for (
                domain,
                name,
                brand_names,
                other_terms,
                entry_info,
            ) in _iter_searchable_integrations(descriptions, title_trans, filter_type):
                if (
                    rank := _rank_integration(
                        query, domain, name, brand_names, other_terms
                    )
                ) is not None:
                    is_yaml_only = not (
                        entry_info.get("config_flow")
                        or "supported_by" in entry_info
                        or "iot_standards" in entry_info
                    )
                    yield (
                        is_yaml_only and rank > 0,
                        rank,
                        name.casefold(),
                        domain,
                        entry_info,
                    )

        best = heapq.nsmallest(_SEARCH_LIMIT, _matches())
        return ToolResult(
            data={
                "result": list[JsonValueType](
                    entry_info for _, _, _, _, entry_info in best
                )
            }
        )


class GetFlowsInProgressTool(Tool):
    """LLM Tool to list pending discovery and reauth config flows."""

    name = "config__get_flows_in_progress"
    title = "Get config flows in progress"
    description = (
        "List non-user-initiated config flows currently in progress, such as "
        "newly discovered devices on the network (zeroconf, bluetooth, ssdp, dhcp, "
        "mqtt, usb) and integrations requiring reauthentication (reauth). Use the "
        "returned flow_id with config__configure_flow to inspect or complete a flow."
    )
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Optional(
                "domain",
                description="Filter in-progress flows by integration domain.",
            ): str,
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """List non-user-initiated flows in progress."""
        args = self.parameters(tool_input.tool_args)
        domain_filter = args.get("domain")
        if domain_filter is not None:
            domain_filter = domain_filter.strip().lower()

        flows = [
            flw
            for flw in hass.config_entries.flow.async_progress()
            if flw["context"].get("source")
            not in (config_entries.SOURCE_RECONFIGURE, config_entries.SOURCE_USER)
            and (domain_filter is None or flw["handler"].lower() == domain_filter)
        ]

        if not flows:
            return ToolResult(data={"result": []})

        lang = (
            llm_context.language
            if llm_context.language and llm_context.language != "*"
            else (hass.config.language or "en")
        )
        handlers = {flw["handler"] for flw in flows}
        config_trans = await translation.async_get_translations(
            hass, lang, "config", handlers
        )
        title_trans = await translation.async_get_translations(
            hass, lang, "title", handlers
        )

        results: list[JsonValueType] = []
        for flw in flows:
            handler = flw["handler"]
            context = flw["context"]
            placeholders = context.get("title_placeholders")
            flow_title = _interpolate_placeholders(
                config_trans.get(f"component.{handler}.config.flow_title"),
                placeholders,
            )
            if not flow_title:
                if isinstance(placeholders, Mapping) and "name" in placeholders:
                    flow_title = str(placeholders["name"])
                else:
                    flow_title = title_trans.get(f"component.{handler}.title", handler)

            item: JsonObjectType = {
                "flow_id": flw["flow_id"],
                "domain": handler,
                "source": context.get("source"),
                "title": flow_title,
            }
            if (step_id := flw.get("step_id")) is not None:
                item["step_id"] = step_id
            if (entry_id := context.get("entry_id")) is not None:
                item["entry_id"] = entry_id
            results.append(item)

        return ToolResult(data={"result": results})


def _build_external_step_error(domain: str, *, is_new_config_flow: bool) -> ToolResult:
    """Return an error ToolResult directing the user to complete OAuth/external setup in the UI."""
    frontend_url = (
        f"/config/integrations/dashboard/add?domain={domain}"
        if is_new_config_flow
        else f"/config/integrations/integration/{domain}"
    )
    return ToolResult(
        data={
            "error": (
                f"Integration '{domain}' requires browser authentication (OAuth / external step) "
                f"and must be configured in the Home Assistant UI at {frontend_url}."
            ),
            "frontend_url": frontend_url,
        },
        error=True,
    )


def _abort_uninitialized_flows(
    flow_mgr: data_entry_flow.FlowManager[Any, Any, Any], handler: Any
) -> None:
    """Abort any uninitialized flows left behind when async_init raises."""
    for flw in flow_mgr.async_progress_by_handler(handler, include_uninitialized=True):
        if "step_id" not in flw:
            with suppress(data_entry_flow.UnknownFlow):
                flow_mgr.async_abort(flw["flow_id"])


async def _async_finalize_flow_step(
    hass: HomeAssistant,
    flow_mgr: data_entry_flow.FlowManager[Any, Any, Any],
    result: data_entry_flow.FlowResult[Any, Any],
    flow_category: FlowCategory,
    domain: str,
    language: str | None,
    *,
    subentry_type: str | None = None,
    is_new_config_flow: bool = False,
) -> ToolResult:
    """Handle external/OAuth steps or format the flow result."""
    flow_type = result["type"]
    if flow_type is data_entry_flow.FlowResultType.EXTERNAL_STEP:
        with suppress(data_entry_flow.UnknownFlow):
            flow_mgr.async_abort(result["flow_id"])
        return _build_external_step_error(domain, is_new_config_flow=is_new_config_flow)
    if (
        flow_type is data_entry_flow.FlowResultType.ABORT
        and result.get("reason") == "missing_credentials"
    ):
        return _build_external_step_error(domain, is_new_config_flow=is_new_config_flow)

    formatted = await async_format_flow_result(
        hass,
        result,
        flow_category,
        domain,
        language,
        subentry_type=subentry_type,
    )
    return ToolResult(data={"result": formatted})


async def _async_start_subentry_flow(
    hass: HomeAssistant,
    entry: ConfigEntry,
    flow_type: str,
    subentry_type: str | None,
    subentry_id: str | None,
    language: str | None,
) -> ToolResult:
    """Start a subentry user or reconfigure flow."""
    if not subentry_type and subentry_id and subentry_id in entry.subentries:
        subentry_type = entry.subentries[subentry_id].subentry_type
    if not subentry_type:
        return ToolResult(
            data={
                "error": "'subentry_type' is required when starting a subentry flow."
            },
            error=True,
        )

    is_reconfigure = bool(subentry_id or flow_type == "reconfigure")
    context: config_entries.SubentryFlowContext = {
        "source": config_entries.SOURCE_RECONFIGURE
        if is_reconfigure
        else config_entries.SOURCE_USER
    }
    if is_reconfigure:
        if not subentry_id:
            return ToolResult(
                data={
                    "error": "'subentry_id' is required when reconfiguring a subentry."
                },
                error=True,
            )
        if subentry_id not in entry.subentries:
            return ToolResult(
                data={"error": f"Subentry '{subentry_id}' not found."},
                error=True,
            )
        context["subentry_id"] = subentry_id

    handler = (entry.entry_id, subentry_type)
    try:
        sub_result = await hass.config_entries.subentries.async_init(
            handler,
            context=context,
        )
    except (
        data_entry_flow.UnknownHandler,
        data_entry_flow.UnknownStep,
        HomeAssistantError,
    ) as err:
        return ToolResult(data={"error": str(err)}, error=True)

    return await _async_finalize_flow_step(
        hass,
        hass.config_entries.subentries,
        sub_result,
        "config_subentries",
        entry.domain,
        language,
        subentry_type=subentry_type,
        is_new_config_flow=False,
    )


class StartFlowTool(Tool):
    """LLM Tool to start a config, options, reconfigure, or subentry flow."""

    name = "config__start_flow"
    title = "Start configuration flow"
    description = (
        "Start a configuration flow in Home Assistant and return its initial step. "
        "Supported flow_type values:\n"
        "- 'config': Set up a new integration or helper instance (requires 'domain').\n"
        "- 'reconfigure': Update core connection, location, or identity settings "
        "of an existing config entry (requires 'entry_id' where supports_reconfigure "
        "is true) or subentry (requires 'entry_id', 'subentry_type', and 'subentry_id').\n"
        "- 'options': Adjust runtime behavior or preferences of an existing config "
        "entry (requires 'entry_id' where supports_options is true).\n"
        "- 'subentry': Add or reconfigure a child item attached to a parent config "
        "entry (requires 'entry_id' and 'subentry_type' from supported_subentry_types; "
        "optionally pass 'subentry_id' to reconfigure)."
    )
    annotations = ToolAnnotations(
        read_only=False, destructive=False, idempotent=False, open_world=True
    )
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required(
                "flow_type",
                description=(
                    "Type of flow to start: 'config' (add new integration), "
                    "'reconfigure' (update core connection/identity settings on an "
                    "entry with supports_reconfigure), 'options' (update runtime "
                    "preferences on an entry with supports_options), or 'subentry' "
                    "(add or reconfigure a child subentry)."
                ),
            ): probatio.All(str, probatio.Lower, probatio.In(_FLOW_TYPES)),
            probatio.Optional(
                "domain",
                description="Integration domain to set up (required when flow_type is 'config').",
            ): str,
            probatio.Optional(
                "entry_id",
                description=(
                    "Config entry ID (required when flow_type is 'options', 'reconfigure', or 'subentry')."
                ),
            ): str,
            probatio.Optional(
                "subentry_type",
                description="Subentry type key from supported_subentry_types (required when flow_type is 'subentry' or reconfiguring a subentry).",
            ): str,
            probatio.Optional(
                "subentry_id",
                description="Existing subentry ID when reconfiguring a subentry.",
            ): str,
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Start the requested flow and return its formatted initial step."""
        args = self.parameters(tool_input.tool_args)
        flow_type: str = args["flow_type"]
        domain: str | None = args.get("domain")
        entry_id: str | None = args.get("entry_id")
        subentry_type: str | None = args.get("subentry_type")
        subentry_id: str | None = args.get("subentry_id")

        if flow_type == "config":
            if not domain or not (domain := domain.strip()):
                return ToolResult(
                    data={"error": "'domain' is required when flow_type is 'config'."},
                    error=True,
                )
            try:
                result = await hass.config_entries.flow.async_init(
                    domain,
                    context={"source": config_entries.SOURCE_USER},
                )
            except data_entry_flow.UnknownHandler:
                return ToolResult(
                    data={
                        "error": f"Integration '{domain}' does not exist or does not support config flows."
                    },
                    error=True,
                )
            except RuntimeError as err:
                if str(err) not in _OAUTH_RUNTIME_ERRORS:
                    raise
                _abort_uninitialized_flows(hass.config_entries.flow, domain)
                return _build_external_step_error(domain, is_new_config_flow=True)
            except data_entry_flow.UnknownStep as err:
                return ToolResult(data={"error": str(err)}, error=True)
            except DependencyError as err:
                return ToolResult(
                    data={
                        "error": f"Failed dependencies: {', '.join(err.failed_dependencies)}"
                    },
                    error=True,
                )
            return await _async_finalize_flow_step(
                hass,
                hass.config_entries.flow,
                result,
                "config",
                domain,
                llm_context.language,
                is_new_config_flow=True,
            )

        if not entry_id:
            return ToolResult(
                data={
                    "error": f"'entry_id' is required when flow_type is '{flow_type}'."
                },
                error=True,
            )
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.source == config_entries.SOURCE_IGNORE:
            return ToolResult(
                data={"error": f"Config entry '{entry_id}' not found."},
                error=True,
            )

        if flow_type == "options":
            if not entry.supports_options:
                return ToolResult(
                    data={
                        "error": f"Config entry '{entry.title}' ({entry.domain}) does not support options."
                    },
                    error=True,
                )
            try:
                result = await hass.config_entries.options.async_init(entry.entry_id)
            except (
                data_entry_flow.UnknownHandler,
                data_entry_flow.UnknownStep,
                HomeAssistantError,
            ) as err:
                return ToolResult(data={"error": str(err)}, error=True)
            return await _async_finalize_flow_step(
                hass,
                hass.config_entries.options,
                result,
                "options",
                entry.domain,
                llm_context.language,
                is_new_config_flow=False,
            )

        if flow_type == "reconfigure" and not subentry_type and not subentry_id:
            if not entry.supports_reconfigure:
                return ToolResult(
                    data={
                        "error": f"Config entry '{entry.title}' ({entry.domain}) does not support reconfigure."
                    },
                    error=True,
                )
            try:
                result = await hass.config_entries.flow.async_init(
                    entry.domain,
                    context={
                        "source": config_entries.SOURCE_RECONFIGURE,
                        "entry_id": entry.entry_id,
                    },
                )
            except RuntimeError as err:
                if str(err) not in _OAUTH_RUNTIME_ERRORS:
                    raise
                _abort_uninitialized_flows(hass.config_entries.flow, entry.domain)
                return _build_external_step_error(
                    entry.domain, is_new_config_flow=False
                )
            except (
                data_entry_flow.UnknownHandler,
                data_entry_flow.UnknownStep,
                HomeAssistantError,
            ) as err:
                return ToolResult(data={"error": str(err)}, error=True)
            return await _async_finalize_flow_step(
                hass,
                hass.config_entries.flow,
                result,
                "config",
                entry.domain,
                llm_context.language,
                is_new_config_flow=False,
            )

        return await _async_start_subentry_flow(
            hass, entry, flow_type, subentry_type, subentry_id, llm_context.language
        )


def _resolve_active_flow(
    hass: HomeAssistant, flow_id: str
) -> (
    tuple[
        data_entry_flow.FlowManager[Any, Any, Any],
        FlowCategory,
        str,
        str | None,
        bool,
    ]
    | None
):
    """Locate which FlowManager owns flow_id and return (manager, category, domain, subentry_type, is_new_config_flow)."""
    try:
        flw = hass.config_entries.flow.async_get(flow_id)
    except data_entry_flow.UnknownFlow:
        pass
    else:
        is_new = flw["context"].get("source") == config_entries.SOURCE_USER
        return (hass.config_entries.flow, "config", str(flw["handler"]), None, is_new)

    try:
        opt_flw = hass.config_entries.options.async_get(flow_id)
    except data_entry_flow.UnknownFlow:
        pass
    else:
        entry_id = str(opt_flw["handler"])
        entry = hass.config_entries.async_get_entry(entry_id)
        domain = entry.domain if entry else entry_id
        return (hass.config_entries.options, "options", domain, None, False)

    try:
        sub_flw = hass.config_entries.subentries.async_get(flow_id)
    except data_entry_flow.UnknownFlow:
        return None
    else:
        entry_id, subentry_type = sub_flw["handler"]
        entry = hass.config_entries.async_get_entry(entry_id)
        domain = entry.domain if entry else entry_id
        return (
            hass.config_entries.subentries,
            "config_subentries",
            domain,
            subentry_type,
            False,
        )


async def _async_handle_invalid_data(
    hass: HomeAssistant,
    flow_mgr: data_entry_flow.FlowManager[Any, Any, Any],
    flow_id: str,
    next_step_id: str | None,
    err: data_entry_flow.InvalidData,
    flow_category: FlowCategory,
    domain: str,
    language: str | None,
    subentry_type: str | None,
    cur_step: data_entry_flow.FlowResult[Any, Any] | None,
) -> ToolResult:
    """Format InvalidData schema errors or step-type mismatch errors."""
    if next_step_id is None and "next_step_id" in err.schema_errors:
        return ToolResult(
            data={
                "error": (
                    "Current step is a 'menu' step; pass the chosen option in "
                    "'next_step_id' instead of 'user_input'."
                )
            },
            error=True,
        )
    if next_step_id is not None and "next_step_id" not in err.schema_errors:
        return ToolResult(
            data={
                "error": (
                    "Current step is a 'form' step; pass field values in "
                    "'user_input' instead of 'next_step_id'."
                )
            },
            error=True,
        )

    partial = flow_mgr.async_get(flow_id)
    step_id = str(partial.get("step_id", "user"))
    step_type = (
        "menu"
        if (cur_step and cur_step.get("type") is data_entry_flow.FlowResultType.MENU)
        or (next_step_id is not None and "next_step_id" in err.schema_errors)
        else "form"
    )
    formatted = await async_format_schema_errors(
        hass,
        flow_id,
        step_id,
        step_type,
        err.schema_errors,
        flow_category,
        domain,
        language,
        subentry_type=subentry_type,
        cur_step=cur_step,
    )
    return ToolResult(data={"result": formatted}, error=True)


class ConfigureFlowTool(Tool):
    """LLM Tool to inspect, advance, or abort an in-progress flow."""

    name = "config__configure_flow"
    title = "Configure flow step"
    description = (
        "Inspect, submit input to, or abort an in-progress configuration, options, "
        "reconfigure, discovery, reauth, or subentry flow by flow_id.\n"
        "- To inspect the current step without submitting, omit both 'user_input' and 'next_step_id'.\n"
        "- To submit a 'form' step, pass field values in 'user_input' (pass {} for "
        "a confirmation form with no fields).\n"
        "- To select an option in a 'menu' step, pass the option key in 'next_step_id'.\n"
        "- To cancel/abort the flow, set 'abort' to true."
    )
    annotations = ToolAnnotations(
        read_only=False, destructive=False, idempotent=False, open_world=True
    )
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required(
                "flow_id",
                description="The flow_id of the in-progress flow.",
            ): probatio.All(str, probatio.NonEmpty()),
            probatio.Optional(
                "user_input",
                description=(
                    "Field values to submit when the current step has type 'form' "
                    "(pass {} to confirm an empty form). Omit to fetch the current step state."
                ),
            ): dict,
            probatio.Optional(
                "next_step_id",
                description=(
                    "Menu option key to select when the current step has type 'menu'."
                ),
            ): probatio.All(str, probatio.NonEmpty()),
            probatio.Optional(
                "abort",
                description="Set to true to cancel and abort the flow (default: false).",
                default=False,
            ): probatio.All(probatio.Boolean(), bool),
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Configure or abort the specified flow."""
        args = self.parameters(tool_input.tool_args)
        flow_id: str = args["flow_id"]
        user_input: dict[str, Any] | None = args.get("user_input")
        next_step_id: str | None = args.get("next_step_id")
        abort: bool = args["abort"]

        if user_input is not None and next_step_id is not None:
            return ToolResult(
                data={"error": "Cannot specify both 'user_input' and 'next_step_id'."},
                error=True,
            )
        if user_input is not None and "next_step_id" in user_input:
            return ToolResult(
                data={
                    "error": (
                        "Use the 'next_step_id' parameter instead of 'user_input' "
                        "to select a menu option."
                    )
                },
                error=True,
            )

        resolved = _resolve_active_flow(hass, flow_id)
        if resolved is None:
            return ToolResult(
                data={"error": f"Flow '{flow_id}' not found."},
                error=True,
            )

        flow_mgr, flow_category, domain, subentry_type, is_new_config_flow = resolved

        if abort:
            flow_mgr.async_abort(flow_id)
            return ToolResult(
                data={
                    "result": {
                        "flow_id": flow_id,
                        "type": "abort",
                        "reason": "aborted",
                    }
                }
            )

        flow_handler = flow_mgr._progress.get(flow_id)  # noqa: SLF001
        cur_step = flow_handler.cur_step if flow_handler is not None else None
        if cur_step is not None:
            if (
                cur_step.get("type") is data_entry_flow.FlowResultType.MENU
                and user_input is not None
            ):
                return ToolResult(
                    data={
                        "error": (
                            "Current step is a 'menu' step; pass the chosen option in "
                            "'next_step_id' instead of 'user_input'."
                        )
                    },
                    error=True,
                )
            if (
                cur_step.get("type") is data_entry_flow.FlowResultType.FORM
                and next_step_id is not None
            ):
                return ToolResult(
                    data={
                        "error": (
                            "Current step is a 'form' step; pass field values in "
                            "'user_input' instead of 'next_step_id'."
                        )
                    },
                    error=True,
                )

        flow_input: dict[str, Any] | None = (
            {"next_step_id": next_step_id} if next_step_id is not None else user_input
        )

        try:
            result = await flow_mgr.async_configure(flow_id, flow_input)
        except data_entry_flow.InvalidData as err:
            return await _async_handle_invalid_data(
                hass,
                flow_mgr,
                flow_id,
                next_step_id,
                err,
                flow_category,
                domain,
                llm_context.language,
                subentry_type,
                cur_step,
            )
        except RuntimeError as err:
            if str(err) not in _OAUTH_RUNTIME_ERRORS:
                raise
            with suppress(data_entry_flow.UnknownFlow):
                flow_mgr.async_abort(flow_id)
            return _build_external_step_error(
                domain, is_new_config_flow=is_new_config_flow
            )
        except data_entry_flow.UnknownFlow:
            return ToolResult(
                data={"error": f"Flow '{flow_id}' not found."},
                error=True,
            )
        except HomeAssistantError as err:
            return ToolResult(data={"error": str(err)}, error=True)

        return await _async_finalize_flow_step(
            hass,
            flow_mgr,
            result,
            flow_category,
            domain,
            llm_context.language,
            subentry_type=subentry_type,
            is_new_config_flow=is_new_config_flow,
        )


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return the config LLM tools for the Home Assistant API."""
    if api_id != LLM_API_HOME_ASSISTANT:
        return None

    return LLMTools(
        tools=[
            GetConfigEntriesTool(),
            ReloadConfigEntryTool(),
            GetFlowsInProgressTool(),
            StartFlowTool(),
            ConfigureFlowTool(),
            SearchIntegrationsTool(),
        ]
    )
