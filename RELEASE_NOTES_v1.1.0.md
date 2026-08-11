# Boogey Lights v1.1.0 — Deterministic Controller State

This release replaces three isolated optimistic entity states with one shared
controller model and makes multi-packet controller operations transactional.

## Fixed

- A zone can no longer remain dark merely because Home Assistant incorrectly
  remembered it as already on. Every ON operation explicitly sends controller
  Power ON, the target zone's RGB-enable ON action, and the RGB state.
- Driver, Passenger, and All now share commanded state. All ON/OFF updates the
  two zone entities it physically affects.
- Multi-packet ON operations cannot interleave with OFF operations.
- A cancelled Home Assistant script cannot abandon a partially applied
  controller transaction.
- All OFF now performs a deterministic shutdown: Zone 1 OFF, Zone 2 OFF,
  All RGB OFF, then controller Power OFF.
- All ON explicitly resets and rebuilds both persistent zone-enable states.

## State semantics

The controller still provides no verified state telemetry, so Home Assistant
state remains the last successfully completed command. Restored state is never
used to skip the Power ON or RGB-enable commands required to normalize the
physical controller.

## Automation guidance

- Use the All entity once for complete shutdown.
- Use the two physical zone entities for split-color scene startup.
- Do not send All OFF and both zone OFF commands in parallel; All OFF already
  performs the complete controller shutdown.
