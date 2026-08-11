from __future__ import annotations

import asyncio
import sys
import types
import unittest


# The packet/transaction layer has very small HA and BLE import surfaces. Stub
# those optional runtime modules so these tests run in an ordinary Python env.
bleak = types.ModuleType("bleak")
bleak.BleakClient = object
sys.modules.setdefault("bleak", bleak)

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
components.bluetooth = bluetooth
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.components", components)
sys.modules.setdefault("homeassistant.components.bluetooth", bluetooth)
sys.modules.setdefault("homeassistant.core", core)
sys.modules.setdefault("homeassistant.config_entries", config_entries)

from custom_components.boogey_lights import boogey  # noqa: E402
from custom_components.boogey_lights.boogey import BoogeyClient  # noqa: E402


class _FakeHass:
    @property
    def loop(self):
        return asyncio.get_running_loop()


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


if __name__ == "__main__":
    unittest.main()
