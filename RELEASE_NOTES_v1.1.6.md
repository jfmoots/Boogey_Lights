# Boogey Lights 1.1.6 — Slow Bluetooth reconnects

- Extend the shared cold-command budget from 30 to 45 seconds for discovery,
  connection, retries, and the first write. An observed successful Bluetooth
  handshake took almost 30 seconds after an idle period.
- Extend the complete operation deadline from 35 to 50 seconds to cover queueing
  and multi-packet commands. A healthy open connection still completes commands
  as soon as their writes finish.
- This release includes the previously unreleased 1.1.5 failure handling and
  command-confirmation diagnostics. See the
  [1.1.5 notes](RELEASE_NOTES_v1.1.5.md) for details.

Update through HACS, then restart Home Assistant to load the new integration.
