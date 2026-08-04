/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stddef.h>
#include <stdint.h>

#define ASTROLABE_PROTOCOL_VERSION 1U
#define ASTROLABE_FIRMWARE_PROTOCOL_REV 1U
#define ASTROLABE_INPUT_KIND_STATE 1U
#define ASTROLABE_INPUT_STATE_BYTES 1U
#define ASTROLABE_INPUT_PACKET_BYTES 6U
#define ASTROLABE_ROTATION_PACKET_BYTES 12U

size_t astrolabe_encode_input_snapshot(uint16_t sequence, uint8_t controls,
                                       uint8_t output[ASTROLABE_INPUT_PACKET_BYTES]);

void astrolabe_encode_rotation(float rx, float ry, float rz,
                               uint8_t output[ASTROLABE_ROTATION_PACKET_BYTES]);
