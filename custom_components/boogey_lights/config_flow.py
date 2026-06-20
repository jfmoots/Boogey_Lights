from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_SELECTION,
    CONF_ZONE_1_NAME,
    CONF_ZONE_2_NAME,
    DEFAULT_NAME,
    DEFAULT_ZONE_1_NAME,
    DEFAULT_ZONE_2_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MANUAL = "manual"


class BoogeyLightsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}

    @callback
    def _get_discovered_devices(self) -> dict[str, str]:
        """Return discovered Boogey-looking BLE devices as address -> label."""
        devices: dict[str, str] = {}
        for info in bluetooth.async_discovered_service_info(self.hass):
            name = info.name or info.local_name or ""
            address = info.address
            if not address:
                continue
            # Boogey GEN2 controllers advertise like BLE#0x609866F31D92.
            # Keep the filter a little broad for clones/firmware variants.
            if name.startswith("BLE#") or "BOOGEY" in name.upper():
                devices[address] = f"{name or 'Boogey Lights'} ({address})"
        return devices

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        self._discovered = self._get_discovered_devices()

        if user_input is not None:
            selection = user_input[CONF_SELECTION]
            if selection == MANUAL:
                return await self.async_step_manual()
            return await self._create_or_name_entry(selection, self._discovered.get(selection, DEFAULT_NAME))

        options = dict(self._discovered)
        options[MANUAL] = "Enter address manually"

        schema = vol.Schema({vol.Required(CONF_SELECTION): vol.In(options)})

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_manual(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            return await self._create_or_name_entry(address, user_input.get(CONF_NAME, DEFAULT_NAME))

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_ADDRESS): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async def _create_or_name_entry(self, address: str, discovered_label: str):
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        default_name = DEFAULT_NAME
        if discovered_label and "(" in discovered_label:
            default_name = discovered_label.split("(", 1)[0].strip() or DEFAULT_NAME

        self.context["title_placeholders"] = {CONF_NAME: default_name}
        self._pending_address = address
        self._pending_name = default_name
        return await self.async_step_options()

    async def async_step_options(self, user_input=None):
        if user_input is not None:
            name = user_input.get(CONF_NAME, self._pending_name)
            return self.async_create_entry(
                title=name,
                data={
                    CONF_NAME: name,
                    CONF_ADDRESS: self._pending_address,
                    CONF_DEVICE_NAME: self._pending_name,
                    CONF_ZONE_1_NAME: user_input.get(CONF_ZONE_1_NAME, DEFAULT_ZONE_1_NAME),
                    CONF_ZONE_2_NAME: user_input.get(CONF_ZONE_2_NAME, DEFAULT_ZONE_2_NAME),
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self._pending_name): str,
                vol.Required(CONF_ZONE_1_NAME, default=DEFAULT_ZONE_1_NAME): str,
                vol.Required(CONF_ZONE_2_NAME, default=DEFAULT_ZONE_2_NAME): str,
            }
        )
        return self.async_show_form(step_id="options", data_schema=schema, errors={})
