/*
 * Astrolabe — production trackball firmware (PLACEHOLDER, not started).
 *
 * Final production freeze remains an open hardware gate in ../../TODO.md. This placeholder does not
 * invent a second pin map. The current SuperMini dual-PMW3610 + five-way board contract lives in
 * ../PMW3610/PMW3610.ino (also the older dual-PMW3389 three-button bench in ../XIAO3389/XIAO3389.ino).
 *
 * The compatibility-frozen BLE rotation and full input-state contracts are documented in
 * ../../docs/ble_device_adapters.md. Production firmware must preserve those wire formats,
 * publish bits 0..4 as Up/Down/Left/Right/Center, use active-low inputs with internal pull-ups and firmware debounce,
 * and publish the full observed bitset if simultaneous inputs occur.
 */

#define ASTROLABE_INPUT_PROTOCOL_VERSION 1
#define ASTROLABE_FIRMWARE_PROTOCOL_REV   1

void setup() {
}

void loop() {
}
