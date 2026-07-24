// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

/*
 * Astrolabe — production trackball firmware (PLACEHOLDER, not started).
 *
 * The product controller is fixed as a Seeed Studio XIAO nRF52840. The remaining sensor, switch,
 * power, and recovery-control freeze remains an open hardware gate in ../../TODO.md, so this
 * placeholder does not invent a final pin map. The completed SuperMini dual-PMW3610 + five-way
 * validation prototype lives in ../PMW3610/PMW3610.ino; the older XIAO dual-PMW3389 three-button
 * protocol bench lives in ../XIAO3389/XIAO3389.ino. Neither sketch is the product assembly.
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
