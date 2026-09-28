# Boogey Lights 1.1.3

This release hardens the first command after an idle BLE release.

- Waits up to 30 seconds for a fresh connectable advertisement instead of
  immediately failing when the controller is absent from Home Assistant's
  Bluetooth cache.
- Uses one bounded deadline for discovery, connection retries, and the GATT
  write.
- Extends the warm connection window to five minutes so scheduled preflight
  commands can hand a live session to the main automation.
- Continues to release idle BLE sessions, avoiding the controller lockup caused
  by indefinitely held connections.
