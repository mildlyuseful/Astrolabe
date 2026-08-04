/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

enum astrolabe_route {
    ASTROLABE_ROUTE_STANDALONE = 0,
    ASTROLABE_ROUTE_BLE_DAEMON,
    ASTROLABE_ROUTE_USB_DAEMON,
};

typedef uint32_t astrolabe_route_lease_t;

#define ASTROLABE_ROUTE_LEASE_NONE 0U

struct astrolabe_route_config {
    float radius_counts;
    float cursor_gain;
    float scroll_gain;
    float scroll_divisor;
    float yaw_deadzone;
    float yaw_dominance;
    uint32_t scroll_hold_ms;
    uint32_t send_interval_ms;
};

int astrolabe_route_init(const struct astrolabe_route_config *config);
enum astrolabe_route astrolabe_route_current(void);
bool astrolabe_route_forced_standalone(void);
int astrolabe_route_claim(enum astrolabe_route route, astrolabe_route_lease_t *lease);
bool astrolabe_route_lease_is_current(enum astrolabe_route route, astrolabe_route_lease_t lease);
void astrolabe_route_release(enum astrolabe_route route, astrolabe_route_lease_t lease);

void astrolabe_route_motion(float wx, float wy, float wz, uint32_t now_ms);
void astrolabe_route_flush(uint32_t now_ms);
void astrolabe_route_control(uint8_t bit_index, uint8_t standalone_buttons, bool pressed);

int astrolabe_route_publish_snapshot(enum astrolabe_route route, astrolabe_route_lease_t lease);

/* Called without the route state mutex before an exact transport lease is
 * replaced. */
void astrolabe_gatt_route_revoked(astrolabe_route_lease_t lease);
void astrolabe_usb_route_revoked(astrolabe_route_lease_t lease);
void astrolabe_gatt_route_available(void);
