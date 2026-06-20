from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import CMD_RGB, CMD_SYSTEM, HEADER, TAIL, WRITE_UUID

_LOGGER = logging.getLogger(__name__)

IDLE_DISCONNECT_SECONDS = 0.0  # v0.4.0: keep the BLE session open; no idle disconnect


def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


def hexstr(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def build_packet(cmd: int, payload: bytes) -> bytes:
    """Build a Boogey Lights GEN2 BLE packet."""
    pkt = bytearray()
    pkt += HEADER
    pkt += bytes([0x01, cmd & 0xFF, len(payload) & 0xFF])
    pkt += payload
    pkt += bytes.fromhex("00 00 00 00")

    bcc = 0
    for byte in pkt:
        bcc ^= byte

    pkt += bytes([bcc])
    pkt += TAIL
    return bytes(pkt)


def system_packet(action: int) -> bytes:
    """Build a Boogey system/action packet.

    Confirmed actions:
      0x10 = controller power off / app grey-screen state
      0x11 = controller power on / restore
      0x20 = zone 1 RGB off
      0x21 = zone 1 RGB on
      0x30 = zone 2 RGB off
      0x31 = zone 2 RGB on
      0x40 = all zones RGB off
      0x41 = all zones RGB on
    """
    return build_packet(CMD_SYSTEM, bytes([action & 0xFF, 0x00, 0x00, 0x00, 0x00]))


def power_packet(on: bool) -> bytes:
    return system_packet(0x11 if on else 0x10)


def rgb_enable_action(channel: int, on: bool) -> int:
    """Return the system action used to enable/disable RGB for a zone."""
    if channel == 0:
        return 0x41 if on else 0x40
    if channel == 1:
        return 0x21 if on else 0x20
    if channel == 2:
        return 0x31 if on else 0x30
    raise ValueError(f"Unsupported Boogey RGB channel: {channel}")


def rgb_enable_packet(channel: int, on: bool) -> bytes:
    return system_packet(rgb_enable_action(channel, on))


def rgb_packet(
    *,
    channel: int,
    red: int,
    green: int,
    blue: int,
    brightness: int,
    effect: int = 1,
    speed: int = 0,
) -> bytes:
    payload = bytes(
        [
            channel & 0xFF,
            effect & 0xFF,
            0x01,  # number of RGB colors for normal RGB mode
            _clamp_byte(speed),
            _clamp_byte(brightness),
            _clamp_byte(red),
            _clamp_byte(green),
            _clamp_byte(blue),
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
    )
    return build_packet(CMD_RGB, payload)


@dataclass
class BoogeyState:
    is_on: bool = False
    red: int = 255
    green: int = 0
    blue: int = 0
    brightness: int = 255
    effect: int = 1
    speed: int = 0


class BoogeyClient:
    """Command client for Boogey Lights GEN2 controllers.

    v0.4.0 keeps a persistent BLE connection open and performs a best-effort
    background preconnect during integration setup. This reduces HomeKit
    spinning/timeouts caused by first-command BLE reconnects.

    It uses the discovered per-zone RGB enable/disable system commands and
    sends controller Power ON before enabling RGB during light turn_on. Power
    OFF is intentionally not used for normal HA light turn_off.
    """

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address
        self._lock = asyncio.Lock()
        self._client: BleakClient | None = None
        self._idle_disconnect_task: asyncio.Task | None = None
        self._startup_connect_task: asyncio.Task | None = None
        self._last_write_monotonic: float = 0.0

    def async_start(self) -> None:
        """Start a best-effort background BLE connection.

        Apple Home is much less patient than Home Assistant when a command has
        to wait for BLE discovery + connect. Keep the controller connected so
        the first HomeKit command is usually a write instead of a full connect.
        """
        if self._startup_connect_task is None or self._startup_connect_task.done():
            self._startup_connect_task = self.hass.loop.create_task(self._startup_connect_worker())

    async def _startup_connect_worker(self) -> None:
        try:
            async with self._lock:
                await self._ensure_connected()
            _LOGGER.info("Boogey startup BLE connection ready for %s", self.address)
        except Exception as err:  # noqa: BLE001
            # Do not fail integration setup if the controller is temporarily out
            # of range. The next command will retry.
            _LOGGER.info("Boogey startup BLE preconnect failed for %s: %s", self.address, err)

    async def async_close(self) -> None:
        """Disconnect and cancel background tasks."""
        if self._startup_connect_task is not None:
            self._startup_connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._startup_connect_task
            self._startup_connect_task = None
        if self._idle_disconnect_task is not None:
            self._idle_disconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_disconnect_task
            self._idle_disconnect_task = None
        await self._disconnect()

    async def _get_ble_device(self):
        """Get the latest connectable BLEDevice from Home Assistant's Bluetooth manager."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass,
            self.address,
            connectable=True,
        )
        if ble_device is None:
            raise RuntimeError(
                f"Boogey controller {self.address} is not currently reachable by a Home Assistant Bluetooth adapter/proxy"
            )
        return ble_device

    async def _ensure_connected(self) -> BleakClient:
        if self._client is not None and self._client.is_connected:
            _LOGGER.debug("Boogey %s reusing existing BLE connection", self.address)
            return self._client

        ble_device = await self._get_ble_device()
        _LOGGER.info("Boogey connect address=%s device=%s", self.address, ble_device)
        start = time.monotonic()
        self._client = await establish_connection(
            BleakClient,
            ble_device,
            self.address,
        )
        _LOGGER.info("Boogey connected address=%s elapsed=%.2fs", self.address, time.monotonic() - start)
        return self._client

    async def _disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is not None and client.is_connected:
            try:
                _LOGGER.debug("Boogey %s disconnecting idle BLE connection", self.address)
                await client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Boogey %s disconnect failed: %s", self.address, err)

    def _schedule_idle_disconnect(self) -> None:
        # v0.4.0: persistent BLE. Do not intentionally disconnect after writes.
        return

    async def _idle_disconnect_worker(self) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            async with self._lock:
                idle_for = time.monotonic() - self._last_write_monotonic
                if idle_for >= IDLE_DISCONNECT_SECONDS:
                    await self._disconnect()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Boogey %s idle disconnect worker failed: %s", self.address, err)

    async def _write_packet_locked(self, label: str, packet: bytes) -> None:
        client = await self._ensure_connected()
        _LOGGER.debug("Boogey TX label=%s address=%s packet=%s", label, self.address, hexstr(packet))
        start = time.monotonic()
        await client.write_gatt_char(WRITE_UUID, packet, response=True)
        self._last_write_monotonic = time.monotonic()
        _LOGGER.debug(
            "Boogey TX complete label=%s elapsed=%.2fs",
            label,
            self._last_write_monotonic - start,
        )
        self._schedule_idle_disconnect()

    async def _write_packet(self, label: str, packet: bytes) -> None:
        async with self._lock:
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    await self._write_packet_locked(label, packet)
                    return
                except Exception as err:  # noqa: BLE001
                    last_error = err
                    _LOGGER.warning(
                        "Boogey write attempt %s failed for %s / %s: %s",
                        attempt,
                        self.address,
                        label,
                        err,
                    )
                    await self._disconnect()
                    await asyncio.sleep(0.25 * attempt)

            assert last_error is not None
            raise last_error

    async def set_power(self, on: bool) -> None:
        await self._write_packet("POWER_ON" if on else "POWER_OFF", power_packet(on))

    async def set_rgb_enabled(self, *, channel: int, on: bool) -> None:
        action = rgb_enable_action(channel, on)
        await self._write_packet(
            f"RGB_{'ON' if on else 'OFF'} ch={channel} action=0x{action:02X}",
            rgb_enable_packet(channel, on),
        )

    async def set_rgb(
        self,
        *,
        channel: int,
        red: int,
        green: int,
        blue: int,
        brightness: int,
        effect: int = 1,
        speed: int = 0,
    ) -> None:
        await self._write_packet(
            f"RGB ch={channel} rgb=({red},{green},{blue}) bri={brightness} effect={effect} speed={speed}",
            rgb_packet(
                channel=channel,
                red=red,
                green=green,
                blue=blue,
                brightness=brightness,
                effect=effect,
                speed=speed,
            ),
        )
