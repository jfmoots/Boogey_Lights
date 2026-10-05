# Stabilization candidate: installation and hardware validation

## Installation

1. Preserve the existing `custom_components/boogey_lights` directory and the
   Holiday participant script outside HA's included package directories.
2. Install this candidate's integration files. Add
   `examples/mooterhome_boogey_shutdown.yaml` as a package only on the Mooterhome
   installation (the entity names in the example are installation-specific).
3. In `mooterhome_holiday_participants_all_off`, replace only the Boogey branch
   with the following. Leave the other participant branches intact:

   ```yaml
   - action: script.turn_on
     target:
       entity_id: script.mooterhome_holiday_boogey_shutdown
   ```

   This intentionally starts recovery independently of the calling script:
   duplicate off-time/window triggers cannot cancel an active recovery. The
   recovery script is single-mode, so duplicates cannot create competing retries.
   Scene preparation/preflight continue using their ordinary one-shot OFF.
4. Validate the HA configuration, then restart HA to load the integration and
   package together. A Python integration update is not activated by reloading
   YAML scripts alone. Keep the ability to restore the previous files and restart.

## Supervised test

- Observe the physical lights; HA only knows successfully sent commands.
- Start Halloween Classic. Confirm both sides alternate orange/purple and
  Courtesy still suppresses Driver when enabled.
- Observe at least two three-minute connection refreshes. Retain the HA Core log
  with `custom_components.boogey_lights: info`; record reconnect reasons, times,
  advertisement source/RSSI, failures, and successful recovery. Protect logs as
  private operational data.
- Close the Holiday window. Confirm other participants shut down immediately and
  both Boogey sides turn off. Both zone entities should be off with
  `command_confirmed: true`. In HA this means command success, not measured light
  output.
- In a supervised maintenance test, make the controller temporarily unreachable
  while a scene is active. Do not alter a shared Bluetooth proxy that other
  essential devices rely on. Confirm the scene continues after expected errors,
  confirmation becomes false, and later phases recover after reachability returns.
- Repeat shutdown with an unreachable controller. Within roughly two minutes
  (three bounded commands plus short waits), HA should post one persistent
  notification. Restore reachability and rerun the shutdown script; successful
  recovery should dismiss it. This is an HA notification, not a phone push.
- Test reopening a show during recovery: the old recovery must stop issuing
  additional OFF commands. An already-started bounded OFF may finish; subsequent
  scene phases must restore the intended colors.

## Full-evening acceptance

Observe one full scheduled show and shutdown with the current connection policy.
Record every unsuccessful reconnect and whether the next scene phase recovers.
Require physical darkness at closing and no unreported failed shutdown. If forced
age refresh is implicated, compare an alternative in a separate change, retaining
all failure-containment fixes. Do not infer radio stability from unit tests.

The recovery script is bounded, not a permanent watchdog. It does not resume
across an HA restart or automatically retry after its three attempts. An alert
remains until a later successful recovery invocation (or manual dismissal).
