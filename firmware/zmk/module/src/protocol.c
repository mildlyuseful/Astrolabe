/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#include <astrolabe/protocol.h>

#include <string.h>

#include <zephyr/sys/byteorder.h>

size_t astrolabe_encode_input_snapshot(uint16_t sequence, uint8_t controls,
                                       uint8_t output[ASTROLABE_INPUT_PACKET_BYTES]) {
    output[0] = ASTROLABE_PROTOCOL_VERSION;
    output[1] = ASTROLABE_INPUT_KIND_STATE;
    output[2] = (uint8_t)sequence;
    output[3] = (uint8_t)(sequence >> 8);
    output[4] = ASTROLABE_INPUT_STATE_BYTES;
    output[5] = controls;
    return ASTROLABE_INPUT_PACKET_BYTES;
}

void astrolabe_encode_rotation(float rx, float ry, float rz,
                               uint8_t output[ASTROLABE_ROTATION_PACKET_BYTES]) {
    const float values[3] = {rx, ry, rz};
    for (size_t i = 0; i < 3; ++i) {
        uint32_t bits;
        memcpy(&bits, &values[i], sizeof(bits));
        sys_put_le32(bits, &output[i * sizeof(bits)]);
    }
}
