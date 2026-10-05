"""Execute the shipped recovery sequence with simulated HA state/service results."""

from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

PACKAGE = Path(__file__).parents[1] / "examples/mooterhome_boogey_shutdown.yaml"
PATIO = "light.patio_passenger_side"
DRIVER = "light.patio_driver_side"


class StopSequence(Exception):
    pass


class RecoverySimulation:
    def __init__(self, outcomes, reopen_after=None):
        self.sequence = yaml.safe_load(PACKAGE.read_text())["script"][
            "mooterhome_holiday_boogey_shutdown"
        ]["sequence"]
        self.outcomes = iter(outcomes)
        self.reopen_after = reopen_after
        self.attempts = 0
        self.notified = False
        self.dismissed = False
        self.reopened = False
        self.state = {PATIO: "on", DRIVER: "on"}
        self.confirmed = {PATIO: False, DRIVER: False}
        self.env = Environment(undefined=StrictUndefined)
        self.env.globals.update(
            is_state=self.is_state,
            state_attr=lambda entity, attr: (
                self.confirmed.get(entity) if attr == "command_confirmed" else None
            ),
            states=lambda entity: self.state.get(entity, "unknown"),
        )

    def is_state(self, entity, expected):
        if entity in self.state:
            return self.state[entity] == expected
        return expected == {
            "input_boolean.mooterhome_holiday_enabled": "on",
            "input_boolean.mooterhome_holiday_window_active": "on"
            if self.reopened
            else "off",
            "input_select.mooterhome_holiday_theme": "Halloween Classic",
        }.get(entity)

    def conditions(self, conditions, context):
        return all(
            self.env.from_string(c["value_template"]).render(**context).strip()
            == "True"
            for c in conditions
        )

    def run(self, steps=None, context=None):
        context = context or {}
        for step in steps if steps is not None else self.sequence:
            if "repeat" in step:
                for index in range(1, step["repeat"]["count"] + 1):
                    self.run(
                        step["repeat"]["sequence"],
                        {**context, "repeat": {"index": index}},
                    )
            elif "if" in step:
                if self.conditions(step["if"], context):
                    self.run(step["then"], context)
            elif "stop" in step:
                raise StopSequence
            elif step.get("action") == "light.turn_off":
                self.attempts += 1
                outcome = next(self.outcomes)
                # Model an expected transport error being handled by HA.
                if outcome == "error":
                    assert step["continue_on_error"] is True
                    self.confirmed = {PATIO: False, DRIVER: False}
                else:
                    a, b, ca, cb = outcome
                    self.state = {PATIO: a, DRIVER: b}
                    self.confirmed = {PATIO: ca, DRIVER: cb}
                if self.reopen_after == self.attempts:
                    self.reopened = True
            elif step.get("action") == "persistent_notification.create":
                self.notified = True
            elif step.get("action") == "persistent_notification.dismiss":
                self.dismissed = True

    def execute(self):
        try:
            self.run()
        except StopSequence:
            pass
        return self


class ShutdownPackageTests(unittest.TestCase):
    def test_first_success_stops_retries(self):
        result = RecoverySimulation([("off", "off", True, True)]).execute()
        self.assertEqual(result.attempts, 1)
        self.assertTrue(result.dismissed)
        self.assertFalse(result.notified)

    def test_error_then_success_recovers(self):
        result = RecoverySimulation(["error", ("off", "off", True, True)]).execute()
        self.assertEqual(result.attempts, 2)
        self.assertFalse(result.notified)

    def test_partial_on_unknown_and_unconfirmed_all_alert(self):
        for outcome in [
            ("off", "on", True, True),
            ("off", "unavailable", True, None),
            ("off", "off", False, True),
            ("off", "off", None, None),
        ]:
            with self.subTest(outcome=outcome):
                result = RecoverySimulation([outcome] * 3).execute()
                self.assertEqual(result.attempts, 3)
                self.assertTrue(result.notified)

    def test_repeated_transport_failures_alert_once(self):
        result = RecoverySimulation(["error"] * 3).execute()
        self.assertEqual(result.attempts, 3)
        self.assertTrue(result.notified)

    def test_new_show_cancels_old_shutdown_retries(self):
        result = RecoverySimulation(["error"], reopen_after=1).execute()
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.notified)

    def test_new_show_during_last_attempt_does_not_alert(self):
        result = RecoverySimulation(["error"] * 3, reopen_after=3).execute()
        self.assertFalse(result.notified)
