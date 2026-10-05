from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CMD_RGB, CMD_SYSTEM, HEADER, TAIL, WRITE_UUID

_LOGGER = logging.getLogger(__name__)

# Keep the session warm long enough for a scheduled preflight and the command
# burst that follows, then release it. This is still intentionally finite: the
# GEN2 controller can wedge when a BLE session is held indefinitely.
# The GEN2 controller can stop accepting new BLE sessions after a connection is
# held indefinitely, and recovering it requires a controller power cycle.
IDLE_DISCONNECT_SECONDS = 300.0
# Traffic during an active effect continually resets the idle timer. Cap the
# total lifetime of a connection as well so a busy show cannot keep one BLE
# session alive long enough for the GEN2 controller to wedge.
MAX_CONNECTION_AGE_SECONDS = 180.0
# A controller that has been idle may not be present in Home Assistant's latest
# connectable-device cache at the instant a command arrives. Wait for a fresh
# advertisement, but put one deadline around discovery, connect, retries, and
# the GATT write so callers never hang indefinitely.
COLD_CONNECT_TIMEOUT_SECONDS = 30.0
OPERATION_TIMEOUT_SECONDS = 35.0
DISCONNECT_TIMEOUT_SECONDS = 5.0
DISCOVERY_POLL_SECONDS = 1.0
WRITE_ATTEMPTS = 3
# A successful GATT write only means the BLE packet was delivered. The GEN2
# controller needs a short processing gap before the next system/RGB command.
INTER_PACKET_DELAY_SECONDS = 0.25


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


class BoogeyCommunicationError(HomeAssistantError):
    """An expected transport failure that HA scripts may safely handle."""


class BoogeyClient:
    """Command client for Boogey Lights GEN2 controllers.

    A best-effort background connection during setup reduces HomeKit
    spinning/timeouts on the first command. The connection is released after
    a short idle period so the controller is not pinned to a stale BLE session.

    It uses the discovered per-zone RGB enable/disable system commands and
    sends controller Power ON before enabling RGB during light turn_on. Power
    OFF is intentionally not used for normal HA light turn_off.
    """

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address
        self._lock = asyncio.Lock()
        # A light operation is a multi-packet controller transaction. Keep the
        # whole sequence serialized so OFF cannot land between Power ON and a
        # later RGB-enable packet from an older ON operation.
        self._operation_lock = asyncio.Lock()
        self._client: BleakClient | None = None
        self._idle_disconnect_task: asyncio.Task | None = None
        self._startup_connect_task: asyncio.Task | None = None
        self._last_write_monotonic: float = 0.0
        self._connected_monotonic: float = 0.0
        self._needs_disconnect = False

    def async_start(self) -> None:
        """Start a best-effort background BLE connection.

        Apple Home is much less patient than Home Assistant when a command has
        to wait for BLE discovery + connect. Keep the controller connected so
        the first HomeKit command is usually a write instead of a full connect.
        """
        if self._startup_connect_task is None or self._startup_connect_task.done():
            self._startup_connect_task = self.hass.loop.create_task(
                self._startup_connect_worker()
            )

    async def _startup_connect_worker(self) -> None:
        try:
            async with self._lock:
                await self._ensure_connected()
                self._schedule_idle_disconnect()
            _LOGGER.info("Boogey startup BLE connection ready for %s", self.address)
        except Exception as err:  # noqa: BLE001
            # Do not fail integration setup if the controller is temporarily out
            # of range. The next command will retry.
            _LOGGER.info(
                "Boogey startup BLE preconnect failed for %s: %s", self.address, err
            )

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

    async def _get_ble_device(self, deadline: float):
        """Wait, within the operation deadline, for a connectable BLEDevice."""
        waiting_logged = False
        while True:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass,
                self.address,
                connectable=True,
            )
            if ble_device is not None:
                if waiting_logged:
                    _LOGGER.info(
                        "Boogey advertisement rediscovered for %s", self.address
                    )
                return ble_device

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoogeyCommunicationError(
                    f"Boogey controller {self.address} did not advertise through a connectable "
                    f"Home Assistant Bluetooth adapter/proxy within "
                    f"{COLD_CONNECT_TIMEOUT_SECONDS:.0f} seconds"
                )

            if not waiting_logged:
                _LOGGER.info(
                    "Boogey %s is not in the connectable-device cache; waiting for an advertisement",
                    self.address,
                )
                waiting_logged = True
            await asyncio.sleep(min(DISCOVERY_POLL_SECONDS, remaining))

    async def _ensure_connected(self, deadline: float | None = None) -> BleakClient:
        if deadline is None:
            deadline = time.monotonic() + COLD_CONNECT_TIMEOUT_SECONDS
        if self._needs_disconnect:
            await self._disconnect(deadline, reason="uncertain_previous_operation")
        if self._client is not None and self._client.is_connected:
            connection_age = time.monotonic() - self._connected_monotonic
            if connection_age < MAX_CONNECTION_AGE_SECONDS:
                _LOGGER.debug(
                    "Boogey %s reusing existing BLE connection age=%.1fs",
                    self.address,
                    connection_age,
                )
                return self._client
            _LOGGER.info(
                "Boogey %s refreshing BLE connection after %.1fs",
                self.address,
                connection_age,
            )
            await self._disconnect(deadline, reason="maximum_age")

        ble_device = await self._get_ble_device(deadline)
        info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        )
        _LOGGER.info(
            "Boogey connect address=%s source=%s rssi=%s device=%s",
            self.address,
            getattr(info, "source", None),
            getattr(info, "rssi", None),
            ble_device,
        )
        start = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoogeyCommunicationError(
                f"Boogey cold-connect deadline expired for {self.address}"
            )
        try:
            async with asyncio.timeout(remaining):
                self._client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self.address,
                )
        except TimeoutError as err:
            raise BoogeyCommunicationError(
                f"Boogey controller {self.address} did not connect within the bounded cold-connect window"
            ) from err
        self._connected_monotonic = time.monotonic()
        _LOGGER.info(
            "Boogey connected address=%s elapsed=%.2fs",
            self.address,
            time.monotonic() - start,
        )
        return self._client

    async def _disconnect(
        self, deadline: float | None = None, *, reason: str = "cleanup"
    ) -> None:
        client = self._client
        self._needs_disconnect = True
        if client is not None and client.is_connected:
            try:
                remaining = DISCONNECT_TIMEOUT_SECONDS
                if deadline is not None:
                    remaining = min(remaining, max(0.0, deadline - time.monotonic()))
                _LOGGER.info(
                    "Boogey disconnect address=%s reason=%s", self.address, reason
                )
                async with asyncio.timeout(remaining):
                    await client.disconnect()
            except (TimeoutError, BleakError, OSError) as err:
                _LOGGER.warning(
                    "Boogey disconnect incomplete address=%s reason=%s error=%s",
                    self.address,
                    reason,
                    err,
                )
                raise BoogeyCommunicationError(
                    f"Boogey disconnect failed: {err}"
                ) from err
        self._client = None
        self._connected_monotonic = 0.0
        self._needs_disconnect = False

    def _schedule_idle_disconnect(self) -> None:
        if self._idle_disconnect_task is not None:
            self._idle_disconnect_task.cancel()
        self._idle_disconnect_task = self.hass.loop.create_task(
            self._idle_disconnect_worker()
        )

    async def _idle_disconnect_worker(self) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            async with self._lock:
                idle_for = time.monotonic() - self._last_write_monotonic
                if idle_for >= IDLE_DISCONNECT_SECONDS:
                    await self._disconnect(reason="idle")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Boogey %s idle disconnect worker failed: %s", self.address, err
            )

    async def _write_packet_locked(
        self, label: str, packet: bytes, deadline: float
    ) -> None:
        client = await self._ensure_connected(deadline)
        _LOGGER.debug(
            "Boogey TX label=%s address=%s packet=%s",
            label,
            self.address,
            hexstr(packet),
        )
        start = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoogeyCommunicationError(
                f"Boogey write deadline expired for {self.address} / {label}"
            )
        try:
            async with asyncio.timeout(remaining):
                await client.write_gatt_char(WRITE_UUID, packet, response=True)
        except TimeoutError as err:
            raise BoogeyCommunicationError(
                f"Boogey GATT write timed out for {self.address} / {label}"
            ) from err
        self._last_write_monotonic = time.monotonic()
        _LOGGER.debug(
            "Boogey TX complete label=%s elapsed=%.2fs",
            label,
            self._last_write_monotonic - start,
        )
        self._schedule_idle_disconnect()

    async def _write_packet(self, label: str, packet: bytes) -> None:
        async with self._lock:
            deadline = time.monotonic() + COLD_CONNECT_TIMEOUT_SECONDS
            last_error: Exception | None = None
            for attempt in range(1, WRITE_ATTEMPTS + 1):
                try:
                    await self._write_packet_locked(label, packet, deadline)
                    return
                except (
                    BoogeyCommunicationError,
                    BleakError,
                    OSError,
                    TimeoutError,
                ) as err:
                    last_error = err
                    _LOGGER.warning(
                        "Boogey write attempt %s failed for %s / %s: %s",
                        attempt,
                        self.address,
                        label,
                        err,
                    )
                    await self._disconnect(deadline, reason="write_failure")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.25 * attempt, remaining))

            assert last_error is not None
            raise BoogeyCommunicationError(
                f"Boogey write failed for {label}: {last_error}"
            ) from last_error

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

    async def _run_transaction(
        self, label: str, steps: list[tuple[str, bytes]]
    ) -> None:
        """Bound queueing and every packet; the coordinator owns cancellation."""
        started = time.monotonic()
        try:
            async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
                async with self._operation_lock:
                    _LOGGER.debug(
                        "Boogey transaction start label=%s steps=%s", label, len(steps)
                    )
                    for index, (step_label, packet) in enumerate(steps):
                        await self._write_packet(step_label, packet)
                        if index < len(steps) - 1:
                            await asyncio.sleep(INTER_PACKET_DELAY_SECONDS)
            _LOGGER.debug(
                "Boogey transaction complete label=%s elapsed=%.2fs",
                label,
                time.monotonic() - started,
            )
        except (TimeoutError, BleakError, OSError) as err:
            self._needs_disconnect = True
            raise BoogeyCommunicationError(
                f"Boogey transaction failed: {label}: {err}"
            ) from err
        except asyncio.CancelledError:
            # A partially written transaction must not keep using a session
            # with uncertain transport state. The next operation reconnects.
            self._needs_disconnect = True
            raise

    async def turn_on_transaction(
        self,
        *,
        channel: int,
        red: int,
        green: int,
        blue: int,
        brightness: int,
        effect: int = 1,
        speed: int = 0,
        zone_1_on: bool = False,
        zone_2_on: bool = False,
    ) -> None:
        """Wake, explicitly enable, and program a target as one operation."""
        state_args = {
            "red": red,
            "green": green,
            "blue": blue,
            "brightness": brightness,
            "effect": effect,
            "speed": speed,
        }

        if channel == 0:
            # Use the controller's native All-enable and All-RGB commands. The
            # processing delay between them is essential; a GATT response is
            # not proof that the controller has processed the prior command.
            steps = [
                ("POWER_ON", power_packet(True)),
                ("RGB_ON ch=0 action=0x41", rgb_enable_packet(0, True)),
                ("RGB ch=0 normalized all state", rgb_packet(channel=0, **state_args)),
            ]
        else:
            # Power ON restores the controller's persistent enable bits. Set
            # both bits to HA's intended state before programming the target,
            # so waking Passenger cannot unexpectedly resurrect Driver.
            steps = [
                ("POWER_ON", power_packet(True)),
                (
                    f"RGB_{'ON' if zone_1_on else 'OFF'} ch=1 action=0x{rgb_enable_action(1, zone_1_on):02X}",
                    rgb_enable_packet(1, zone_1_on),
                ),
                (
                    f"RGB_{'ON' if zone_2_on else 'OFF'} ch=2 action=0x{rgb_enable_action(2, zone_2_on):02X}",
                    rgb_enable_packet(2, zone_2_on),
                ),
                (
                    f"RGB ch={channel} normalized state",
                    rgb_packet(channel=channel, **state_args),
                ),
            ]

        await self._run_transaction(f"TURN_ON ch={channel}", steps)

    async def update_rgb_transaction(
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
        """Program an already-enabled target with one serialized RGB packet."""
        await self._run_transaction(
            f"UPDATE_RGB ch={channel}",
            [
                (
                    f"RGB ch={channel} state update",
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
            ],
        )

    async def turn_off_transaction(self, *, channel: int) -> None:
        """Disable one zone, or deterministically shut down the controller."""
        if channel == 0:
            steps = [
                # Put the native All-OFF command first. Even if a later cleanup
                # command is lost, the first accepted command darkens both zones.
                ("RGB_OFF ch=0 action=0x40", rgb_enable_packet(0, False)),
                ("RGB_OFF ch=1 action=0x20", rgb_enable_packet(1, False)),
                ("RGB_OFF ch=2 action=0x30", rgb_enable_packet(2, False)),
                ("POWER_OFF", power_packet(False)),
            ]
            label = "SHUTDOWN_ALL"
        else:
            action = rgb_enable_action(channel, False)
            steps = [
                (
                    f"RGB_OFF ch={channel} action=0x{action:02X}",
                    rgb_enable_packet(channel, False),
                ),
            ]
            label = f"TURN_OFF ch={channel}"

        await self._run_transaction(label, steps)
