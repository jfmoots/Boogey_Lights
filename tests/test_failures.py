"""Failure-path regression tests; no controller or HA services are touched."""

import asyncio
import time
import unittest
from unittest.mock import patch

import test_transactions as support  # Install the minimal HA/BLE stubs.
from custom_components.boogey_lights import boogey, light
from homeassistant.exceptions import HomeAssistantError


class TransportFailures(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = boogey.BoogeyClient(support._FakeHass(), "test")

    async def asyncTearDown(self):
        # Tests that deliberately hang disconnect must not hang test cleanup.
        self.client._client = None
        await self.client.async_close()

    async def test_disconnect_timeout_retains_client_for_cleanup(self):
        class Hung(support._FakeBleakClient):
            async def disconnect(self):
                await asyncio.Event().wait()

        connected = Hung()
        self.client._client = connected
        with patch.object(boogey, "DISCONNECT_TIMEOUT_SECONDS", 0.02):
            with self.assertRaises(HomeAssistantError):
                await asyncio.wait_for(self.client._disconnect(), 0.3)
        self.assertIs(self.client._client, connected)
        self.assertTrue(self.client._needs_disconnect)

    async def test_failed_refresh_is_a_handleable_ha_error(self):
        self.client._client = support._FakeBleakClient()
        self.client._connected_monotonic = time.monotonic() - 181

        async def device(deadline):
            return object()

        async def connect(*args, **kwargs):
            raise support.bleak_exc.BleakError("proxy unreachable")

        self.client._get_ble_device = device
        with (
            patch.object(boogey, "establish_connection", connect),
            patch.object(boogey, "WRITE_ATTEMPTS", 1),
        ):
            with self.assertRaisesRegex(HomeAssistantError, "proxy unreachable"):
                await self.client.update_rgb_transaction(
                    channel=1, red=1, green=2, blue=3, brightness=100
                )

    async def test_whole_transaction_deadline_covers_multiple_packets(self):
        sent = []

        async def slow(label, packet):
            sent.append(label)
            await asyncio.sleep(0.025)

        self.client._write_packet = slow
        with (
            patch.object(boogey, "OPERATION_TIMEOUT_SECONDS", 0.04),
            patch.object(boogey, "INTER_PACKET_DELAY_SECONDS", 0),
        ):
            with self.assertRaises(HomeAssistantError):
                await asyncio.wait_for(self.client.turn_off_transaction(channel=0), 0.3)
        self.assertLess(len(sent), 4)
        self.assertTrue(self.client._needs_disconnect)

    async def test_transport_lock_wait_is_bounded(self):
        await self.client._operation_lock.acquire()
        try:
            with patch.object(boogey, "OPERATION_TIMEOUT_SECONDS", 0.02):
                with self.assertRaises(HomeAssistantError):
                    await asyncio.wait_for(
                        self.client.turn_off_transaction(channel=0), 0.3
                    )
        finally:
            self.client._operation_lock.release()

    async def test_unexpected_programming_error_is_not_swallowed(self):
        async def broken(label, packet):
            raise ValueError("invalid packet")

        self.client._write_packet = broken
        with self.assertRaisesRegex(ValueError, "invalid packet"):
            await self.client.turn_off_transaction(channel=0)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.fail = False

    async def turn_on_transaction(self, **kwargs):
        await self.update_rgb_transaction(**kwargs)

    async def update_rgb_transaction(self, **kwargs):
        self.calls.append(("on", kwargs))
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise boogey.BoogeyCommunicationError("controller unavailable")

    async def turn_off_transaction(self, **kwargs):
        self.calls.append(("off", kwargs))
        if self.fail:
            raise boogey.BoogeyCommunicationError("controller unavailable")


class CoordinatorFailures(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.coordinator = light.BoogeyCoordinator(self.transport)
        self.entity = light.BoogeyLight(
            self.coordinator, "test", "Passenger", 1, "zone1"
        )

    async def test_failed_color_does_not_change_successfully_sent_state(self):
        await self.entity.async_turn_on(
            rgb_color=(255, 20, 0), brightness=170, effect="Steady"
        )
        self.transport.fail = True
        with self.assertRaises(HomeAssistantError):
            await self.entity.async_turn_on(
                rgb_color=(110, 0, 180), brightness=130, effect="Steady"
            )
        self.assertEqual(self.entity.rgb_color, (255, 20, 0))
        self.assertEqual(self.entity.brightness, 170)
        self.assertTrue(self.entity.is_on)
        self.assertFalse(self.entity.extra_state_attributes["command_confirmed"])
        self.assertIn(
            "unavailable", self.entity.extra_state_attributes["last_transport_error"]
        )
        self.transport.fail = False
        await self.entity.async_turn_on(rgb_color=(1, 2, 3), effect="Steady")
        self.assertTrue(self.entity.extra_state_attributes["command_confirmed"])
        self.assertIsNone(self.entity.extra_state_attributes["last_transport_error"])

    async def test_failed_shutdown_is_unconfirmed_even_if_previous_state_off(self):
        self.transport.fail = True
        with self.assertRaises(HomeAssistantError):
            await self.entity.async_turn_off()
        self.assertFalse(self.entity.extra_state_attributes["command_confirmed"])

    async def test_cancelled_active_on_commits_before_shutdown(self):
        self.transport.block = True
        on = asyncio.create_task(self.entity.async_turn_on(rgb_color=(1, 2, 3)))
        await self.transport.started.wait()
        on.cancel()
        await asyncio.sleep(0)
        on.cancel()  # A second stop must not abandon the controller worker.
        off = asyncio.create_task(self.coordinator.async_turn_off(0))
        await asyncio.sleep(0)
        self.assertEqual(len(self.transport.calls), 1)
        self.transport.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await on
        await off
        self.assertEqual([name for name, _ in self.transport.calls], ["on", "off"])
        self.assertFalse(self.entity.is_on)
        self.assertTrue(self.coordinator.command_confirmed[1])

    async def test_cancelled_queued_on_never_runs(self):
        await self.coordinator._state_lock.acquire()
        queued = asyncio.create_task(self.entity.async_turn_on())
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.coordinator._state_lock.release()
        await asyncio.sleep(0)
        self.assertEqual(self.transport.calls, [])

    async def test_off_invalidates_queued_old_color(self):
        self.transport.block = True
        first = asyncio.create_task(self.coordinator.async_apply(1, {"red": 1}))
        await self.transport.started.wait()
        stale = asyncio.create_task(self.coordinator.async_apply(1, {"red": 2}))
        await asyncio.sleep(0)
        off = asyncio.create_task(self.coordinator.async_turn_off(0))
        await asyncio.sleep(0)
        self.transport.release.set()
        await asyncio.gather(first, stale, off)
        self.assertEqual([name for name, _ in self.transport.calls], ["on", "off"])
        self.assertFalse(self.entity.is_on)

    async def test_shutdown_after_stalled_command_can_complete(self):
        self.transport.block = True
        with patch.object(light, "OPERATION_TIMEOUT_SECONDS", 0.04):
            first = asyncio.create_task(self.entity.async_turn_on())
            await self.transport.started.wait()
            await asyncio.sleep(0.015)
            off = asyncio.create_task(self.coordinator.async_turn_off(0))
            with self.assertRaises(HomeAssistantError):
                await first
            await off
        self.assertFalse(self.entity.is_on)
        self.assertTrue(self.coordinator.command_confirmed[1])

    async def test_coordinator_queue_wait_is_bounded(self):
        await self.coordinator._state_lock.acquire()
        try:
            with patch.object(light, "OPERATION_TIMEOUT_SECONDS", 0.02):
                with self.assertRaises(HomeAssistantError):
                    await asyncio.wait_for(self.entity.async_turn_off(), 0.3)
        finally:
            self.coordinator._state_lock.release()
        self.assertEqual(self.transport.calls, [])

    async def test_slider_burst_merges_fields_without_early_state_commit(self):
        await self.entity.async_turn_on(rgb_color=(1, 2, 3), effect="Steady")
        with patch.object(light, "RGB_DEBOUNCE_SECONDS", 0.01):
            await self.entity.async_turn_on(rgb_color=(4, 5, 6))
            await self.entity.async_turn_on(brightness=80)
            self.assertEqual(self.entity.rgb_color, (1, 2, 3))
            await self.entity._pending_rgb_task
        self.assertEqual(self.entity.rgb_color, (4, 5, 6))
        self.assertEqual(self.entity.brightness, 80)
        self.assertEqual(len(self.transport.calls), 2)

    async def test_off_cancels_debounced_color(self):
        await self.entity.async_turn_on()
        await self.entity.async_turn_on(rgb_color=(4, 5, 6))
        await self.entity.async_turn_off()
        await asyncio.sleep(0.01)
        self.assertEqual([name for name, _ in self.transport.calls], ["on", "off"])
        self.assertFalse(self.entity.is_on)

    async def test_zone_off_cancels_pending_all_color(self):
        all_entity = light.BoogeyLight(self.coordinator, "test", "All", 0, "all")
        await all_entity.async_turn_on()
        await all_entity.async_turn_on(rgb_color=(4, 5, 6))
        await self.entity.async_turn_off()
        await asyncio.sleep(0.01)
        self.assertEqual([name for name, _ in self.transport.calls], ["on", "off"])
        self.assertFalse(self.entity.is_on)
