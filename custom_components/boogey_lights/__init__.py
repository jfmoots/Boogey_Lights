from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .boogey import BoogeyClient
from .const import CONF_ADDRESS, DOMAIN

PLATFORMS = ["light"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    client = BoogeyClient(hass, entry.data[CONF_ADDRESS])
    hass.data[DOMAIN][entry.entry_id] = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    client.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client = hass.data[DOMAIN].pop(entry.entry_id, None)
        if client is not None:
            await client.async_close()
    return unload_ok
