from __future__ import annotations

import asyncio
import contextlib
import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .boogey import BoogeyClient, BoogeyState
from .const import (
    CHANNEL_1,
    CHANNEL_2,
    CHANNEL_ALL,
    CONF_ZONE_1_NAME,
    CONF_ZONE_2_NAME,
    DOMAIN,
    EFFECT_NAMES,
    EFFECTS,
)

_LOGGER = logging.getLogger(__name__)

# HA's color picker can generate a rapid stream of turn_on calls while dragging.
# The Boogey controller is slow enough that sending every intermediate color
# creates a long BLE backlog. Debounce normal color/brightness/effect changes
# and only send the latest requested state.
RGB_DEBOUNCE_SECONDS = 0.60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    client: BoogeyClient = hass.data[DOMAIN][entry.entry_id]
    base_name = entry.data[CONF_NAME]
    zone_1_name = entry.data[CONF_ZONE_1_NAME]
    zone_2_name = entry.data[CONF_ZONE_2_NAME]

    # Channel mapping learned from v0.1.9 diagnostics:
    #   channel 0 = all zones
    #   channel 1 = passenger/front/rear zone
    #   channel 2 = driver-side zone
    async_add_entities(
        [
            BoogeyLight(client, entry.entry_id, f"{base_name} All", CHANNEL_ALL, "all"),
            BoogeyLight(client, entry.entry_id, f"{base_name} {zone_1_name}", CHANNEL_1, "zone_1"),
            BoogeyLight(client, entry.entry_id, f"{base_name} {zone_2_name}", CHANNEL_2, "zone_2"),
        ]
    )


class BoogeyLight(LightEntity, RestoreEntity):
    _attr_has_entity_name = False
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_effect_list = EFFECT_NAMES
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(
        self,
        client: BoogeyClient,
        entry_id: str,
        name: str,
        channel: int,
        suffix: str,
    ) -> None:
        self._client = client
        self._channel = channel
        self._state = BoogeyState()
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{suffix}"
        self._pending_rgb_task: asyncio.Task | None = None
        self._pending_rgb_seq = 0

    @property
    def is_on(self) -> bool:
        return self._state.is_on

    @property
    def brightness(self) -> int:
        return self._state.brightness

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return (self._state.red, self._state.green, self._state.blue)

    @property
    def effect(self) -> str | None:
        for name, value in EFFECTS.items():
            if value == self._state.effect:
                return name
        return "Steady"

    async def async_added_to_hass(self) -> None:
        """Restore the last HA-side state after restart.

        The controller does not provide telemetry, so this only restores the
        last state Home Assistant commanded. It prevents every restart from
        falling back to the default red/100% assumed state.
        """
        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        self._state.is_on = last_state.state == "on"

        attrs = last_state.attributes
        rgb = attrs.get("rgb_color")
        if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
            self._state.red = int(rgb[0])
            self._state.green = int(rgb[1])
            self._state.blue = int(rgb[2])

        brightness = attrs.get("brightness")
        if brightness is not None:
            self._state.brightness = int(brightness)

        effect = attrs.get("effect")
        if effect in EFFECTS:
            self._state.effect = EFFECTS[effect]

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_pending_rgb()

    def _cancel_pending_rgb(self) -> None:
        self._pending_rgb_seq += 1
        if self._pending_rgb_task is not None:
            self._pending_rgb_task.cancel()
            self._pending_rgb_task = None

    def _schedule_debounced_rgb(self) -> None:
        self._pending_rgb_seq += 1
        seq = self._pending_rgb_seq
        if self._pending_rgb_task is not None:
            self._pending_rgb_task.cancel()
        self._pending_rgb_task = asyncio.create_task(self._debounced_rgb_worker(seq))

    async def _debounced_rgb_worker(self, seq: int) -> None:
        try:
            await asyncio.sleep(RGB_DEBOUNCE_SECONDS)
            if seq != self._pending_rgb_seq or not self._state.is_on:
                return
            await self._send_rgb("DEBOUNCED_RGB")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Boogey debounced RGB send failed name=%s channel=%s: %s", self._attr_name, self._channel, err)
        finally:
            if seq == self._pending_rgb_seq:
                self._pending_rgb_task = None

    async def _send_rgb(self, reason: str) -> None:
        _LOGGER.debug(
            "Boogey entity send_rgb reason=%s name=%s channel=%s rgb=(%s,%s,%s) brightness=%s effect=%s speed=%s",
            reason,
            self._attr_name,
            self._channel,
            self._state.red,
            self._state.green,
            self._state.blue,
            self._state.brightness,
            self._state.effect,
            self._state.speed,
        )
        await self._client.set_rgb(
            channel=self._channel,
            red=self._state.red,
            green=self._state.green,
            blue=self._state.blue,
            brightness=self._state.brightness,
            effect=self._state.effect,
            speed=self._state.speed,
        )

    async def async_turn_on(self, **kwargs) -> None:
        was_off = not self._state.is_on

        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            self._state.red = int(red)
            self._state.green = int(green)
            self._state.blue = int(blue)

        if ATTR_BRIGHTNESS in kwargs:
            self._state.brightness = int(kwargs[ATTR_BRIGHTNESS])

        if ATTR_EFFECT in kwargs:
            self._state.effect = EFFECTS.get(kwargs[ATTR_EFFECT], 1)

        if self._state.brightness <= 0:
            self._state.brightness = 255

        self._state.is_on = True
        self.async_write_ha_state()

        _LOGGER.debug(
            "Boogey entity turn_on name=%s channel=%s was_off=%s kwargs=%s rgb=(%s,%s,%s) brightness=%s effect=%s",
            self._attr_name,
            self._channel,
            was_off,
            kwargs,
            self._state.red,
            self._state.green,
            self._state.blue,
            self._state.brightness,
            self._state.effect,
        )

        if was_off:
            # v0.3.1: Boogey has two layers of state:
            #   0x11 = controller-level Power ON / wake / restore
            #   0x21/0x31/0x41 = RGB enable for Zone 1 / Zone 2 / All
            # Testing showed that if the official app leaves the controller in
            # the grey-screen Power OFF state, RGB enable alone is not enough.
            # So HA turn_on wakes the controller first, then enables the requested
            # RGB channel, then sends the requested color/brightness/effect.
            # Normal turn_off still uses only RGB OFF and does not send Power OFF.
            self._cancel_pending_rgb()
            await self._client.set_power(True)
            await self._client.set_rgb_enabled(channel=self._channel, on=True)
            await self._send_rgb("IMMEDIATE_POWER_ON_RGB_ENABLE_THEN_STATE")
            return

        # Color picker / brightness slider updates while already on: coalesce.
        self._schedule_debounced_rgb()

    async def async_turn_off(self, **kwargs) -> None:
        # OFF is high priority: cancel any queued color-wheel updates and send now.
        self._cancel_pending_rgb()
        _LOGGER.debug(
            "Boogey entity turn_off name=%s channel=%s kwargs=%s",
            self._attr_name,
            self._channel,
            kwargs,
        )
        # v0.3.0: turn off by sending the proven per-zone RGB OFF system action.
        # This mirrors the official app's RGB OFF button and preserves the
        # controller's higher-level power state.
        await self._client.set_rgb_enabled(channel=self._channel, on=False)
        self._state.is_on = False
        self.async_write_ha_state()
