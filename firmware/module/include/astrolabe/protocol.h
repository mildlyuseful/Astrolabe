/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * The Astrolabe wire contract: what a device sends a daemon, over BLE GATT or USB vendor HID.
 * Any device that emits these packets is an Astrolabe controller. docs/protocol.md is the prose
 * version of this file; change both together.
 */

#pragma once

#include <stdint.h>
#include <string.h>

#include <zephyr/sys/byteorder.h>

#define ASTROLABE_PROTOCOL_VERSION 1U
#define ASTROLABE_FIRMWARE_PROTOCOL_REV 1U

/* Rotation: three little-endian float32 increments, in radians, about the housing axes. */
#define ASTROLABE_ROTATION_BYTES 12U

/* Input snapshot: version, kind, u16 sequence, state length, then the control bitmask. */
#define ASTROLABE_SNAPSHOT_KIND_STATE 1U
#define ASTROLABE_SNAPSHOT_STATE_BYTES 1U
#define ASTROLABE_SNAPSHOT_BYTES (5U + ASTROLABE_SNAPSHOT_STATE_BYTES)
#define ASTROLABE_MAX_CONTROLS (8U * ASTROLABE_SNAPSHOT_STATE_BYTES)

/* BLE: one primary service. A client claims the route by subscribing to rotation and writing the
 * keepalive at least every timeout/3; the keepalive read reports (version, u16 timeout ms). */
#define ASTROLABE_UUID(n) BT_UUID_128_ENCODE(0x2cad0000 + (n), 0x6e64, 0x0146, 0xb139, 0x9cf2a4cd57fc)
#define ASTROLABE_UUID_SERVICE ASTROLABE_UUID(1)
#define ASTROLABE_UUID_ROTATION ASTROLABE_UUID(2)
#define ASTROLABE_UUID_INPUT ASTROLABE_UUID(3)
#define ASTROLABE_UUID_KEEPALIVE ASTROLABE_UUID(4)

static inline void astrolabe_encode_rotation(const float rotation[3],
                                             uint8_t out[ASTROLABE_ROTATION_BYTES]) {
    for (int i = 0; i < 3; ++i) {
        uint32_t bits;
        memcpy(&bits, &rotation[i], sizeof(bits));
        sys_put_le32(bits, &out[i * 4]);
    }
}

static inline void astrolabe_encode_snapshot(uint16_t sequence, uint8_t controls,
                                             uint8_t out[ASTROLABE_SNAPSHOT_BYTES]) {
    out[0] = ASTROLABE_PROTOCOL_VERSION;
    out[1] = ASTROLABE_SNAPSHOT_KIND_STATE;
    sys_put_le16(sequence, &out[2]);
    out[4] = ASTROLABE_SNAPSHOT_STATE_BYTES;
    out[5] = controls;
}
