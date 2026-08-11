# Boogey Lights v1.1.1 — Controller Pacing and Unambiguous Zone State

This release follows physical-controller testing of v1.1.0. BLE write responses
arrived successfully, but the controller sometimes failed to process later
commands in a rapid multi-packet operation.

## Fixed

- Multi-packet operations now pause 250 ms between commands so the controller
  can process each system/RGB action.
- All ON uses Power ON, native All RGB ON, then one All RGB state packet.
- All OFF puts native All RGB OFF first, followed by per-zone cleanup and Power
  OFF. If only the first command is processed, both zones still turn off.
- Waking one zone normalizes both persistent zone-enable bits to the intended HA
  state, preventing Power ON from unexpectedly restoring the other zone.
- Color/effect test-button actions on an already-on zone send one immediate RGB
  packet instead of a delayed wake/enable/color sequence.
- All Zones reports on only when both physical zones are commanded on; partial
  states remain visible through the individual zone entities.

## State semantics

The controller provides no verified telemetry. HA continues to report the last
successfully completed command. Dashboard controls should use separate All ON
and All OFF actions rather than an aggregate toggle when zones may be in a
partial state.
