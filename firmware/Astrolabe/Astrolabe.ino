/*
 * Astrolabe — production trackball firmware (PLACEHOLDER, not started).
 *
 * This sketch is the planned successor to firmware/XIAO3389/XIAO3389.ino, which is the
 * dual-PMW3389 TEST-BENCH firmware. The production build targets different sensors
 * (PMW3610-class — see HANDOFF.md "PMW3610 third sensor") and will take over this name
 * when the bench hardware is retired; XIAO3389 moves to archive/ at that point.
 *
 * Until then there is nothing here: flash XIAO3389.ino for all current work. The BLE
 * rotation protocol (service 2cad0001-…, 12-byte float32 rx/ry/rz notify) is the contract
 * the daemon depends on — the production firmware must keep speaking it.
 */

void setup() {
}

void loop() {
}
