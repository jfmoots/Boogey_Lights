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
RGB_DEBOUNCE_SECONDS = 0.15


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    client: BoogeyClient = hass.data[DOMAIN][entry.entry_id]
    base_name = entry.data[CONF_NAME]
    zone_1_name = entry.data[CONF_ZONE_1_NAME]
    zone_2_name = entry.data[CONF_ZONE_2_NAME]
    coordinator = BoogeyCoordinator(client)

    # Channel mapping learned from v0.1.9 diagnostics:
    #   channel 0 = all zones
    #   channel 1 = passenger/front/rear zone
    #   channel 2 = driver-side zone
    async_add_entities(
        [
            BoogeyLight(coordinator, entry.entry_id, f"{base_name} All", CHANNEL_ALL, "all"),
            BoogeyLight(coordinator, entry.entry_id, f"{base_name} {zone_1_name}", CHANNEL_1, "zone_1"),
            BoogeyLight(coordinator, entry.entry_id, f"{base_name} {zone_2_name}", CHANNEL_2, "zone_2"),
        ]
    )


class BoogeyCoordinator:
    """One commanded-state model shared by all entities for a controller."""

    def __init__(self, client: BoogeyClient) -> None:
        self.client = client
        self.states = {
            CHANNEL_ALL: BoogeyState(),
            CHANNEL_1: BoogeyState(),
            CHANNEL_2: BoogeyState(),
        }
        self.entities: list[BoogeyLight] = []

    def register(self, entity: "BoogeyLight") -> None:
        self.entities.append(entity)

    def is_on(self, channel: int) -> bool:
        if channel == CHANNEL_ALL:
            # "All Zones on" has one unambiguous meaning: both physical zones
            # are on. A partial state remains visible on the individual entity.
            return self.states[CHANNEL_1].is_on and self.states[CHANNEL_2].is_on
        return self.states[channel].is_on

    def cancel_pending(self, channel: int) -> None:
        for entity in self.entities:
            if channel == CHANNEL_ALL or entity._channel == channel:
                entity._cancel_pending_rgb()

    def notify(self) -> None:
        for entity in self.entities:
            if getattr(entity, "hass", None) is not None:
                entity.async_write_ha_state()

    @staticmethod
    def _copy_rgb_state(source: BoogeyState, target: BoogeyState) -> None:
        target.red = source.red
        target.green = source.green
        target.blue = source.blue
        target.brightness = source.brightness
        target.effect = source.effect
        target.speed = source.speed

    async def async_turn_on(self, channel: int) -> None:
        state = self.states[channel]
        zone_1_on = True if channel in (CHANNEL_ALL, CHANNEL_1) else self.states[CHANNEL_1].is_on
        zone_2_on = True if channel in (CHANNEL_ALL, CHANNEL_2) else self.states[CHANNEL_2].is_on
        await self.client.turn_on_transaction(
            channel=channel,
            red=state.red,
            green=state.green,
            blue=state.blue,
            brightness=state.brightness,
            effect=state.effect,
            speed=state.speed,
            zone_1_on=zone_1_on,
            zone_2_on=zone_2_on,
        )

        if channel == CHANNEL_ALL:
            for zone_channel in (CHANNEL_1, CHANNEL_2):
                zone_state = self.states[zone_channel]
                self._copy_rgb_state(state, zone_state)
                zone_state.is_on = True
            state.is_on = True
        else:
            state.is_on = True
            self.states[CHANNEL_ALL].is_on = self.is_on(CHANNEL_ALL)
        self.notify()

    async def async_update_rgb(self, channel: int) -> None:
        state = self.states[channel]
        await self.client.update_rgb_transaction(
            channel=channel,
            red=state.red,
            green=state.green,
            blue=state.blue,
            brightness=state.brightness,
            effect=state.effect,
            speed=state.speed,
        )
        if channel == CHANNEL_ALL:
            for zone_channel in (CHANNEL_1, CHANNEL_2):
                self._copy_rgb_state(state, self.states[zone_channel])
        self.notify()

    async def async_turn_off(self, channel: int) -> None:
        self.cancel_pending(channel)
        await self.client.turn_off_transaction(channel=channel)
        if channel == CHANNEL_ALL:
            self.states[CHANNEL_ALL].is_on = False
            self.states[CHANNEL_1].is_on = False
            self.states[CHANNEL_2].is_on = False
        else:
            self.states[channel].is_on = False
            self.states[CHANNEL_ALL].is_on = self.is_on(CHANNEL_ALL)
        self.notify()


class BoogeyLight(LightEntity, RestoreEntity):
    _attr_has_entity_name = False
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_effect_list = EFFECT_NAMES
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(
        self,
        coordinator: BoogeyCoordinator,
        entry_id: str,
        name: str,
        channel: int,
        suffix: str,
    ) -> None:
        self._coordinator = coordinator
        self._channel = channel
        self._state = coordinator.states[channel]
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{suffix}"
        self._pending_rgb_task: asyncio.Task | None = None
        self._pending_rgb_seq = 0
        coordinator.register(self)

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_on(self._channel)

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

        # All is a derived bulk target. Restoring its old on/off flag must not
        # overwrite the separately restored physical-zone command memories.
        if self._channel != CHANNEL_ALL:
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
            if seq != self._pending_rgb_seq or not self.is_on:
                return
            await self._send_rgb_update("DEBOUNCED_RGB_STATE")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Boogey debounced RGB send failed name=%s channel=%s: %s", self._attr_name, self._channel, err)
        finally:
            if seq == self._pending_rgb_seq:
                self._pending_rgb_task = None

    async def _send_on_transaction(self, reason: str) -> None:
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
        await self._coordinator.async_turn_on(self._channel)

    async def _send_rgb_update(self, reason: str) -> None:
        _LOGGER.debug("Boogey entity RGB-only update reason=%s name=%s channel=%s", reason, self._attr_name, self._channel)
        await self._coordinator.async_update_rgb(self._channel)

    async def async_turn_on(self, **kwargs) -> None:
        was_off = not self.is_on

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

        if was_off or not kwargs:
            # Every ON transaction explicitly wakes, enables, and programs the
            # target. Never skip controller normalization based on restored HA
            # state; the factory app/RF remote can change the real enable bits.
            self._cancel_pending_rgb()
            await self._send_on_transaction("IMMEDIATE_NORMALIZED_TURN_ON")
            return

        # An explicit effect is typical of a scene/test button. Send its one
        # RGB packet immediately. Color-wheel/slider streams still coalesce.
        if ATTR_EFFECT in kwargs:
            self._cancel_pending_rgb()
            await self._send_rgb_update("IMMEDIATE_EXPLICIT_RGB_STATE")
            return

        self._schedule_debounced_rgb()

    async def async_turn_off(self, **kwargs) -> None:
        # OFF is high priority: cancel any queued color-wheel updates and send now.
        self._coordinator.cancel_pending(self._channel)
        _LOGGER.debug(
            "Boogey entity turn_off name=%s channel=%s kwargs=%s",
            self._attr_name,
            self._channel,
            kwargs,
        )
        # Individual zones use their proven RGB OFF action. All performs the
        # complete deterministic shutdown, including master Power OFF.
        await self._coordinator.async_turn_off(self._channel)
