/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdint.h>

int astrolabe_gatt_rotation(const uint8_t payload[12]);
int astrolabe_gatt_snapshot(const uint8_t payload[6]);
int astrolabe_usb_rotation(const uint8_t payload[12]);
int astrolabe_usb_snapshot(const uint8_t payload[6]);
