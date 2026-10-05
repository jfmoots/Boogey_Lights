"""Run separately with the real Home Assistant 2026.9.4 dependencies installed."""

import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import sys

import yaml
from homeassistant.core import Context, HomeAssistant
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA
from homeassistant.helpers.script import Script

sys.path.insert(0, str(Path(__file__).parents[1]))
from custom_components.boogey_lights.boogey import BoogeyCommunicationError


async def main():
    package = yaml.safe_load(
        (
            Path(__file__).parents[1] / "examples/mooterhome_boogey_shutdown.yaml"
        ).read_text()
    )
    config = package["script"]["mooterhome_holiday_boogey_shutdown"]
    with tempfile.TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        SCRIPT_ENTITY_SCHEMA(deepcopy(config))
        print("PASS: recovery package validates against real HA script schema")
        completed = []

        async def fail(call):
            raise BoogeyCommunicationError("simulated Bluetooth loss")

        async def succeed(call):
            completed.append(True)

        hass.services.async_register("boogey_test", "fail", fail)
        hass.services.async_register("boogey_test", "succeed", succeed)
        script = Script(
            hass,
            [
                {"action": "boogey_test.fail", "continue_on_error": True},
                {"action": "boogey_test.succeed"},
            ],
            "Bluetooth failure containment",
            "script",
        )
        await script.async_run(context=Context())
        assert completed == [True]
        print("PASS: real HA continues the scene after BoogeyCommunicationError")

        # Exercise the actual shipped recovery sequence in HA's script engine.
        # Replace only delays so hardware-free failure tests finish promptly.
        def no_delays(node):
            if isinstance(node, dict):
                return {k: 0 if k == "delay" else no_delays(v) for k, v in node.items()}
            if isinstance(node, list):
                return [no_delays(v) for v in node]
            return node

        patio = "light.mooterhome_holiday_zone_patio"
        driver = "light.mooterhome_holiday_zone_driver"
        for label, outcomes, reopen, expected_attempts, expected_alerts in [
            ("recover after failure", ["error", "off"], False, 2, 0),
            ("alert after three failures", ["error"] * 3, False, 3, 1),
            ("unavailable is not off", ["unavailable"] * 3, False, 3, 1),
            (
                "off without confirmation is not success",
                ["unconfirmed"] * 3,
                False,
                3,
                1,
            ),
            ("reopened show cancels retries", ["error"], True, 1, 0),
        ]:
            attempts = []
            alerts = []
            result_iter = iter(outcomes)
            hass.states.async_set("input_boolean.mooterhome_holiday_enabled", "on")
            hass.states.async_set(
                "input_boolean.mooterhome_holiday_window_active", "off"
            )
            hass.states.async_set(
                "input_select.mooterhome_holiday_theme", "Halloween Classic"
            )
            hass.states.async_set("light.patio_underglow", "on")
            for entity in (patio, driver):
                hass.states.async_set(entity, "on", {"command_confirmed": False})

            async def turn_off(call):
                attempts.append(True)
                result = next(result_iter)
                if reopen:
                    hass.states.async_set(
                        "input_boolean.mooterhome_holiday_window_active", "on"
                    )
                if result == "error":
                    raise BoogeyCommunicationError("simulated unreachable controller")
                state = "off" if result in ("off", "unconfirmed") else result
                for entity in (patio, driver):
                    hass.states.async_set(
                        entity, state, {"command_confirmed": result == "off"}
                    )

            async def notify(call):
                alerts.append(call.data)

            async def dismiss(call):
                pass

            hass.services.async_register("light", "turn_off", turn_off)
            hass.services.async_register("persistent_notification", "create", notify)
            hass.services.async_register("persistent_notification", "dismiss", dismiss)
            validated = SCRIPT_ENTITY_SCHEMA(no_delays(deepcopy(config)))
            recovery = Script(hass, validated["sequence"], label, "script")
            await recovery.async_run(context=Context())
            assert len(attempts) == expected_attempts, (label, attempts)
            assert len(alerts) == expected_alerts, (label, alerts)
            print(f"PASS: real HA recovery: {label}")
        await hass.async_stop()


asyncio.run(main())
