# Boogey Lights v1.1.2 — BLE Idle Release

This release changes the controller connection from permanently held to warm
on demand. A field failure showed that leaving the GEN2 controller connected
overnight could wedge its Bluetooth stack until its 12-volt supply was
power-cycled.

## Fixed

- BLE connections are released after 60 seconds without a command.
- Each successful write resets the idle timer, preserving fast reuse during a
  Home Assistant or Apple Home command burst.
- The best-effort startup preconnection is subject to the same idle release.
- Existing write retry and reconnection behavior remains unchanged.

## Why 60 seconds

The short warm period preserves the low-latency benefit of connection reuse for
related commands while avoiding an indefinite connection that can leave the
controller unable to accept a new session.
