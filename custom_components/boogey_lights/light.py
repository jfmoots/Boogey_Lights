from __future__ import annotations

import asyncio
import time
from dataclasses import replace
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

from .boogey import (
    BoogeyClient,
    BoogeyState,
    BoogeyCommunicationError,
    OPERATION_TIMEOUT_SECONDS,
)
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
    #   channel 1 = passenger-side zone
    #   channel 2 = driver-side zone
    async_add_entities(
        [
            BoogeyLight(
                coordinator, entry.entry_id, f"{base_name} All", CHANNEL_ALL, "all"
            ),
            BoogeyLight(
                coordinator,
                entry.entry_id,
                f"{base_name} {zone_1_name}",
                CHANNEL_1,
                "zone_1",
            ),
            BoogeyLight(
                coordinator,
                entry.entry_id,
                f"{base_name} {zone_2_name}",
                CHANNEL_2,
                "zone_2",
            ),
        ]
    )


class BoogeyCoordinator:
    """One commanded-state model shared by all entities for a controller."""

    def __init__(self, client: BoogeyClient) -> None:
        self.client = client
        # State decisions and their controller transactions must be atomic.
        # Two zones are commonly started in parallel by HA scenes; without
        # this lock both could snapshot the other as OFF, and the transaction
        # that ran second would disable the zone that had just started.
        self._state_lock = asyncio.Lock()
        self.states = {
            CHANNEL_ALL: BoogeyState(),
            CHANNEL_1: BoogeyState(),
            CHANNEL_2: BoogeyState(),
        }
        self.entities: list[BoogeyLight] = []
        self._generation = {CHANNEL_ALL: 0, CHANNEL_1: 0, CHANNEL_2: 0}
        self.last_error: str | None = None
        self.last_success: float | None = None
        self.command_confirmed = {
            CHANNEL_ALL: False,
            CHANNEL_1: False,
            CHANNEL_2: False,
        }

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
            if channel == CHANNEL_ALL or entity._channel in (channel, CHANNEL_ALL):
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

    async def _execute(self, operation) -> None:
        """Finish an active operation AND its state commit before cancellation.

        A cancelled queued command must never wake the controller later. Once
        started, its bounded worker owns the lock through the state commit.
        Repeated cancellation cannot abandon that worker during shutdown.
        """
        started = False

        async def worker():
            nonlocal started
            try:
                async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
                    async with self._state_lock:
                        started = True
                        await operation()
            except TimeoutError as err:
                self.command_confirmed = dict.fromkeys(self.command_confirmed, False)
                self.last_error = "Boogey operation timed out (including queue wait)"
                self.notify()
                raise BoogeyCommunicationError(self.last_error) from err
            except BoogeyCommunicationError as err:
                self.last_error = str(err)
                self.notify()
                raise

        task = asyncio.create_task(worker())
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                if not started:
                    task.cancel()
            except Exception:
                break
        if cancelled:
            if not task.cancelled():
                # Retrieve a failed worker's exception without masking the
                # caller's cancellation; the worker already recorded it.
                task.exception()
            raise asyncio.CancelledError
        task.result()

    async def async_apply(
        self, channel: int, changes: dict, *, normalize: bool = False
    ) -> None:
        generation = self._generation[channel]
        changes = dict(changes)

        async def operation():
            if generation != self._generation[channel]:
                return  # An OFF superseded this queued scene/color command.
            target = replace(self.states[channel], **changes)
            args = dict(
                channel=channel,
                red=target.red,
                green=target.green,
                blue=target.blue,
                brightness=target.brightness,
                effect=target.effect,
                speed=target.speed,
            )
            affected = (
                (CHANNEL_1, CHANNEL_2, CHANNEL_ALL)
                if channel == CHANNEL_ALL
                else (channel, CHANNEL_ALL)
            )
            for zone in affected:
                self.command_confirmed[zone] = False
            try:
                if normalize or not self.is_on(channel):
                    await self.client.turn_on_transaction(
                        **args,
                        zone_1_on=channel in (CHANNEL_ALL, CHANNEL_1)
                        or self.is_on(CHANNEL_1),
                        zone_2_on=channel in (CHANNEL_ALL, CHANNEL_2)
                        or self.is_on(CHANNEL_2),
                    )
                else:
                    await self.client.update_rgb_transaction(**args)
            except BaseException:
                self.notify()
                raise
            self._copy_rgb_state(target, self.states[channel])
            self.states[channel].is_on = True
            if channel == CHANNEL_ALL:
                for zone in (CHANNEL_1, CHANNEL_2):
                    self._copy_rgb_state(target, self.states[zone])
                    self.states[zone].is_on = True
            self.states[CHANNEL_ALL].is_on = self.is_on(CHANNEL_ALL)
            self.command_confirmed[channel] = True
            if channel == CHANNEL_ALL:
                self.command_confirmed[CHANNEL_1] = self.command_confirmed[
                    CHANNEL_2
                ] = True
            self.command_confirmed[CHANNEL_ALL] = all(
                self.command_confirmed[z] for z in (CHANNEL_1, CHANNEL_2)
            )
            self.last_error = None
            self.last_success = time.time()
            self.notify()

        await self._execute(operation)

    async def async_turn_on(self, channel: int) -> None:
        await self.async_apply(channel, {}, normalize=True)

    async def async_update_rgb(self, channel: int) -> None:
        await self.async_apply(channel, {})

    async def async_turn_off(self, channel: int) -> None:
        self.cancel_pending(channel)
        # Invalidate queued ONs for every target that overlaps this OFF.
        for zone in (
            (CHANNEL_ALL, CHANNEL_1, CHANNEL_2)
            if channel == CHANNEL_ALL
            else (channel, CHANNEL_ALL)
        ):
            self._generation[zone] += 1

        async def operation():
            affected = (
                (CHANNEL_ALL, CHANNEL_1, CHANNEL_2)
                if channel == CHANNEL_ALL
                else (channel, CHANNEL_ALL)
            )
            for zone in affected:
                self.command_confirmed[zone] = False
            try:
                await self.client.turn_off_transaction(channel=channel)
            except BaseException:
                self.notify()
                raise
            for zone in (
                (CHANNEL_1, CHANNEL_2) if channel == CHANNEL_ALL else (channel,)
            ):
                self.states[zone].is_on = False
                self.command_confirmed[zone] = True
            self.states[CHANNEL_ALL].is_on = self.is_on(CHANNEL_ALL)
            self.command_confirmed[CHANNEL_ALL] = all(
                self.command_confirmed[z] for z in (CHANNEL_1, CHANNEL_2)
            )
            self.last_error = None
            self.last_success = time.time()
            self.notify()

        await self._execute(operation)


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
        self._pending_rgb_changes: dict = {}
        coordinator.register(self)

    @property
    def extra_state_attributes(self):
        return {
            "command_confirmed": self._coordinator.command_confirmed[self._channel],
            "last_transport_error": self._coordinator.last_error,
            "last_successful_command": self._coordinator.last_success,
        }

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
        self._pending_rgb_changes = {}
        if self._pending_rgb_task is not None:
            self._pending_rgb_task.cancel()
            self._pending_rgb_task = None

    def _schedule_debounced_rgb(self, changes: dict) -> None:
        pending = {**self._pending_rgb_changes, **changes}
        self._cancel_pending_rgb()
        self._pending_rgb_changes = pending
        seq = self._pending_rgb_seq
        self._pending_rgb_task = asyncio.create_task(self._debounced_rgb_worker(seq))

    async def _debounced_rgb_worker(self, seq: int) -> None:
        try:
            await asyncio.sleep(RGB_DEBOUNCE_SECONDS)
            if seq != self._pending_rgb_seq or not self.is_on:
                return
            changes = self._pending_rgb_changes
            self._pending_rgb_changes = {}
            await self._coordinator.async_apply(self._channel, changes)
        except asyncio.CancelledError:
            raise
        except BoogeyCommunicationError as err:
            _LOGGER.warning(
                "Boogey debounced RGB send failed name=%s channel=%s: %s",
                self._attr_name,
                self._channel,
                err,
            )
        finally:
            if seq == self._pending_rgb_seq:
                self._pending_rgb_task = None

    async def async_turn_on(self, **kwargs) -> None:
        changes = {}
        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            changes.update(red=int(red), green=int(green), blue=int(blue))
        if ATTR_BRIGHTNESS in kwargs:
            changes["brightness"] = max(1, int(kwargs[ATTR_BRIGHTNESS]))
        if ATTR_EFFECT in kwargs:
            changes["effect"] = EFFECTS.get(kwargs[ATTR_EFFECT], 1)
        if not self.is_on or not kwargs or ATTR_EFFECT in kwargs:
            # Merge pending slider fields into this explicit request, without
            # ever changing the successfully sent state before completion.
            changes = {**self._pending_rgb_changes, **changes}
            self._cancel_pending_rgb()
            await self._coordinator.async_apply(
                self._channel, changes, normalize=not kwargs
            )
        else:
            self._schedule_debounced_rgb(changes)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_turn_off(self._channel)
