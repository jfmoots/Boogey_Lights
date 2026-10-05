from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest


# The packet/transaction layer has very small HA and BLE import surfaces. Stub
# those optional runtime modules so these tests run in an ordinary Python env.
bleak = types.ModuleType("bleak")
bleak.BleakClient = object
sys.modules.setdefault("bleak", bleak)
bleak_exc = types.ModuleType("bleak.exc")
bleak_exc.BleakError = type("BleakError", (Exception,), {})
sys.modules.setdefault("bleak.exc", bleak_exc)
ha_exceptions = types.ModuleType("homeassistant.exceptions")
ha_exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
sys.modules.setdefault("homeassistant.exceptions", ha_exceptions)

retry = types.ModuleType("bleak_retry_connector")
retry.establish_connection = None
sys.modules.setdefault("bleak_retry_connector", retry)

homeassistant = types.ModuleType("homeassistant")
components = types.ModuleType("homeassistant.components")
bluetooth = types.ModuleType("homeassistant.components.bluetooth")
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
config_entries = types.ModuleType("homeassistant.config_entries")
config_entries.ConfigEntry = object
ha_const = types.ModuleType("homeassistant.const")
ha_const.CONF_NAME = "name"
helpers = types.ModuleType("homeassistant.helpers")
entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
entity_platform.AddEntitiesCallback = object
restore_state = types.ModuleType("homeassistant.helpers.restore_state")
restore_state.RestoreEntity = type("RestoreEntity", (), {})
light_component = types.ModuleType("homeassistant.components.light")
light_component.ATTR_BRIGHTNESS = "brightness"
light_component.ATTR_EFFECT = "effect"
light_component.ATTR_RGB_COLOR = "rgb_color"
light_component.ColorMode = types.SimpleNamespace(RGB="rgb")
light_component.LightEntity = type("LightEntity", (), {})
light_component.LightEntityFeature = types.SimpleNamespace(EFFECT=1)
bluetooth.async_last_service_info = lambda *args, **kwargs: None
components.bluetooth = bluetooth
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.components", components)
sys.modules.setdefault("homeassistant.components.bluetooth", bluetooth)
sys.modules.setdefault("homeassistant.components.light", light_component)
sys.modules.setdefault("homeassistant.core", core)
sys.modules.setdefault("homeassistant.config_entries", config_entries)
sys.modules.setdefault("homeassistant.const", ha_const)
sys.modules.setdefault("homeassistant.helpers", helpers)
sys.modules.setdefault("homeassistant.helpers.entity_platform", entity_platform)
sys.modules.setdefault("homeassistant.helpers.restore_state", restore_state)

from custom_components.boogey_lights import boogey  # noqa: E402
from custom_components.boogey_lights.boogey import BoogeyClient  # noqa: E402
from custom_components.boogey_lights.const import CHANNEL_1, CHANNEL_2  # noqa: E402
from custom_components.boogey_lights.light import BoogeyCoordinator  # noqa: E402


class _FakeHass:
    @property
    def loop(self):
        return asyncio.get_running_loop()


class _FakeBleakClient:
    def __init__(self) -> None:
        self.is_connected = True
        self.disconnect_calls = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.is_connected = False


class _RecordingTransactionClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def turn_on_transaction(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        boogey.INTER_PACKET_DELAY_SECONDS = 0
        self.client = BoogeyClient(_FakeHass(), "test-controller")
        self.labels: list[str] = []

        async def record(label: str, packet: bytes) -> None:
            self.labels.append(label)
            await asyncio.sleep(0)

        self.client._write_packet = record

    async def test_all_on_uses_native_all_commands(self) -> None:
        await self.client.turn_on_transaction(
            channel=0,
            red=1,
            green=2,
            blue=3,
            brightness=4,
        )
        self.assertEqual(
            self.labels,
            [
                "POWER_ON",
                "RGB_ON ch=0 action=0x41",
                "RGB ch=0 normalized all state",
            ],
        )

    async def test_all_off_is_full_shutdown(self) -> None:
        await self.client.turn_off_transaction(channel=0)
        self.assertEqual(
            self.labels,
            [
                "RGB_OFF ch=0 action=0x40",
                "RGB_OFF ch=1 action=0x20",
                "RGB_OFF ch=2 action=0x30",
                "POWER_OFF",
            ],
        )

    async def test_operations_do_not_interleave(self) -> None:
        on = asyncio.create_task(
            self.client.turn_on_transaction(
                channel=2,
                red=1,
                green=2,
                blue=3,
                brightness=4,
                zone_2_on=True,
            )
        )
        await asyncio.sleep(0)
        off = asyncio.create_task(self.client.turn_off_transaction(channel=2))
        await asyncio.gather(on, off)
        self.assertEqual(
            self.labels,
            [
                "POWER_ON",
                "RGB_OFF ch=1 action=0x20",
                "RGB_ON ch=2 action=0x31",
                "RGB ch=2 normalized state",
                "RGB_OFF ch=2 action=0x30",
            ],
        )

    async def test_rgb_update_is_one_packet(self) -> None:
        await self.client.update_rgb_transaction(
            channel=1,
            red=255,
            green=0,
            blue=0,
            brightness=255,
        )
        self.assertEqual(self.labels, ["RGB ch=1 state update"])


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_zone_start_uses_updated_other_zone_state(self) -> None:
        client = _RecordingTransactionClient()
        coordinator = BoogeyCoordinator(client)

        passenger = asyncio.create_task(coordinator.async_turn_on(CHANNEL_1))
        await client.first_started.wait()
        driver = asyncio.create_task(coordinator.async_turn_on(CHANNEL_2))
        await asyncio.sleep(0)

        self.assertEqual(len(client.calls), 1)
        client.release_first.set()
        await asyncio.gather(passenger, driver)

        self.assertFalse(client.calls[0]["zone_2_on"])
        self.assertTrue(client.calls[1]["zone_1_on"])
        self.assertTrue(client.calls[1]["zone_2_on"])


class IdleDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_idle_seconds = boogey.IDLE_DISCONNECT_SECONDS
        self.client = BoogeyClient(_FakeHass(), "test-controller")

    async def asyncTearDown(self) -> None:
        boogey.IDLE_DISCONNECT_SECONDS = self.original_idle_seconds
        await self.client.async_close()

    async def test_idle_connection_is_released(self) -> None:
        boogey.IDLE_DISCONNECT_SECONDS = 0
        bleak_client = _FakeBleakClient()
        self.client._client = bleak_client
        self.client._last_write_monotonic = time.monotonic()

        self.client._schedule_idle_disconnect()
        await self.client._idle_disconnect_task

        self.assertIsNone(self.client._client)
        self.assertEqual(bleak_client.disconnect_calls, 1)

    async def test_rescheduling_replaces_idle_timer(self) -> None:
        boogey.IDLE_DISCONNECT_SECONDS = 60

        self.client._schedule_idle_disconnect()
        first_task = self.client._idle_disconnect_task
        self.client._schedule_idle_disconnect()
        second_task = self.client._idle_disconnect_task
        await asyncio.sleep(0)

        self.assertIsNot(first_task, second_task)
        self.assertTrue(first_task.cancelled())
        self.assertFalse(second_task.done())


class ConnectionAgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_max_age = boogey.MAX_CONNECTION_AGE_SECONDS
        self.original_establish_connection = boogey.establish_connection
        self.client = BoogeyClient(_FakeHass(), "test-controller")

    async def asyncTearDown(self) -> None:
        boogey.MAX_CONNECTION_AGE_SECONDS = self.original_max_age
        boogey.establish_connection = self.original_establish_connection
        await self.client.async_close()

    async def test_connection_is_reused_before_maximum_age(self) -> None:
        existing = _FakeBleakClient()
        self.client._client = existing
        self.client._connected_monotonic = time.monotonic()

        connected = await self.client._ensure_connected()

        self.assertIs(connected, existing)
        self.assertEqual(existing.disconnect_calls, 0)

    async def test_connection_is_refreshed_after_maximum_age(self) -> None:
        boogey.MAX_CONNECTION_AGE_SECONDS = 60
        existing = _FakeBleakClient()
        replacement = _FakeBleakClient()
        self.client._client = existing
        self.client._connected_monotonic = time.monotonic() - 61

        async def get_device(deadline: float):
            return object()

        async def connect(*args, **kwargs):
            return replacement

        self.client._get_ble_device = get_device
        boogey.establish_connection = connect

        connected = await self.client._ensure_connected()

        self.assertIs(connected, replacement)
        self.assertEqual(existing.disconnect_calls, 1)
        self.assertGreater(self.client._connected_monotonic, 0)


class ColdConnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_poll_seconds = boogey.DISCOVERY_POLL_SECONDS
        boogey.DISCOVERY_POLL_SECONDS = 0
        self.client = BoogeyClient(_FakeHass(), "test-controller")

    async def asyncTearDown(self) -> None:
        boogey.DISCOVERY_POLL_SECONDS = self.original_poll_seconds
        await self.client.async_close()

    async def test_waits_for_fresh_advertisement(self) -> None:
        expected_device = object()
        discoveries = iter([None, None, expected_device])
        bluetooth.async_ble_device_from_address = lambda *args, **kwargs: next(
            discoveries
        )

        device = await self.client._get_ble_device(time.monotonic() + 1)

        self.assertIs(device, expected_device)

    async def test_discovery_wait_has_a_deadline(self) -> None:
        bluetooth.async_ble_device_from_address = lambda *args, **kwargs: None

        with self.assertRaisesRegex(
            boogey.BoogeyCommunicationError, "did not advertise"
        ):
            await self.client._get_ble_device(time.monotonic())


if __name__ == "__main__":
    unittest.main()
