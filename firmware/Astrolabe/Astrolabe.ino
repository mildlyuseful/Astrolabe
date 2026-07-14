/*
 * Astrolabe — production trackball firmware (PLACEHOLDER, not started).
 *
 * This sketch is the planned successor to firmware/XIAO3389/XIAO3389.ino, which is the
 * dual-PMW3389 TEST-BENCH firmware. The production build targets different sensors
 * (PMW3610-class — see HANDOFF.md "PMW3610 third sensor") and will take over this name
 * when the bench hardware is retired; XIAO3389 moves to archive/ at that point.
 *
 * Until then there is nothing here: flash XIAO3389.ino for all current work. The BLE
 * rotation protocol (service 2cad0001-…, characteristic 2cad0002-…, fixed 12-byte
 * float32 rx/ry/rz notify) is the contract the daemon depends on and must remain unchanged.
 * Phase 6 additionally reserves characteristic 2cad0003-… for protocol-v1 full input-state
 * snapshots. The production implementation must map bits 0..4 to Up/Down/Left/Right/Center,
 * but this placeholder intentionally does not invent pins, polarity, or debounce for hardware
 * that is not documented in the repository.
 */

#define ASTROLABE_INPUT_PROTOCOL_VERSION 1
#define ASTROLABE_FIRMWARE_PROTOCOL_REV   1

void setup() {
}

void loop() {
}
