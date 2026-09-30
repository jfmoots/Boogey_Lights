# Boogey Lights 1.1.4

This release prevents an active lighting effect from keeping one Bluetooth
session alive indefinitely.

Version 1.1.3 kept a connection warm for five minutes of inactivity so a
scheduled preflight could bridge into the main lighting start. During an active
show, frequent color commands continually reset that idle timer. A controller
could therefore remain connected for the entire show and eventually stop
responding while Home Assistant still reported successful commanded state.

Version 1.1.4 adds an independent three-minute maximum connection lifetime.
The next command after that limit cleanly disconnects and reconnects before
writing. The existing five-minute idle release remains in place, and the new
limit is still long enough to preserve the two-minute scheduled preflight.

It also serializes the shared commanded-state decision with each zone's full
controller transaction. When Home Assistant starts Passenger and Driver in
parallel, the second transaction now sees the first zone's completed state
instead of accidentally switching it back off.

Tests cover reuse of a young connection, forced refresh of an expired one, and
parallel two-zone startup.
