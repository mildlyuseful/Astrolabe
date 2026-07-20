/*
 * Astrolabe — production trackball firmware (PLACEHOLDER, not started).
 *
 * Final sensor count, sensor model, geometry, buses, pins, and switch debounce remain open
 * hardware gates in ../../TODO.md. This placeholder does not invent final pins or debounce timing.
 * Current bench work uses ../XIAO3389/XIAO3389.ino.
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
