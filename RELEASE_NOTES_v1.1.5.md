# Boogey Lights 1.1.5 — Failure containment

This stabilization release keeps the five-minute idle timeout, three-minute
connection-age refresh, and 30-second cold-connect limit unchanged. It addresses
what happens when Bluetooth fails rather than selecting another connection policy.

- Expected Bluetooth, OS transport, and timeout failures become Home Assistant
  service errors, allowing `continue_on_error` to work in Holiday scripts.
- A 35-second operation budget includes coordinator queueing, transport queueing,
  connection, retries, packet processing delays, and state commit. Disconnect has
  its own five-second cap and remains subject to the caller's remaining budget.
- Requested colors are snapshots. Shared color/brightness memory is committed
  only after a successful operation; failed writes cannot masquerade as success.
- A cancelled queued command never executes later. An active command finishes
  within its budget and commits state before releasing its lock; repeated caller
  cancellation cannot detach it. OFF invalidates older queued color commands.
- Entity attributes expose `command_confirmed`, `last_transport_error`, and
  `last_successful_command` (Unix timestamp). Confirmation means transport success,
  **not physical feedback**; the controller still has no verified state telemetry.
  Confirmation starts false after HA restart and becomes false when an operation
  fails or times out. Aggregate confirmation requires both zones.
- INFO logs include disconnect reason, connection age at forced refresh, and
  advertisement source/RSSI when attempting a connection. The advertisement source
  is diagnostic context, not a guarantee of the backend's final connection route.
- An optional Mooterhome package retries shutdown three times, checks BOTH zone
  states and confirmation, stops if a new show opens, and raises a persistent HA
  notification on unresolved failure. It must be wired into the existing
  participants-all-off script as described in the example and validation guide.

## Validation and limits

Failure tests cover reconnect failure, stalled disconnect, queue/transaction
budgets, state preservation, repeated cancellation, stale queued updates, shutdown
behind a stalled command, and recovery-package outcomes. They do not validate
controller radio behavior. Follow `docs/stabilization-validation.md` before
promoting this candidate after a full evening of hardware observation.
