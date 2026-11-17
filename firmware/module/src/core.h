/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Module-internal interfaces between the route (route.c), the standalone mouse (mouse.c), the
 * daemon transports (ble.c, usb.c), and the status LED (indicator.c).
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <astrolabe/protocol.h>
#include <zephyr/sys/util.h>
#include <zmk/endpoints_types.h>

/* Who receives motion and controls. Standalone means the device is its own HID mouse. */
enum astrolabe_route {
    ASTROLABE_ROUTE_STANDALONE,
    ASTROLABE_ROUTE_BLE,
    ASTROLABE_ROUTE_USB,
    ASTROLABE_ROUTE_COUNT,
};

/* Every claim mints a new lease. A transport holds it and passes it back, so a callback that
 * belongs to an ownership the route has since moved on from becomes a no-op. */
typedef uint32_t astrolabe_lease_t;
#define ASTROLABE_LEASE_NONE 0U

struct astrolabe_transport {
    /* Send to the transport's current session. Non-zero leaves the data queued for a retry. */
    int (*send_rotation)(const uint8_t payload[ASTROLABE_ROTATION_BYTES]);
    int (*send_snapshot)(const uint8_t payload[ASTROLABE_SNAPSHOT_BYTES]);
    /* The route took this lease away (another transport claimed). Called without route locks. */
    void (*revoked)(astrolabe_lease_t lease);
};

void astrolabe_route_register(enum astrolabe_route route, const struct astrolabe_transport *api);

/* -EPERM when booted into forced standalone; -EBUSY when BLE asks while USB owns the route. */
int astrolabe_route_claim(enum astrolabe_route route, astrolabe_lease_t *lease);
void astrolabe_route_release(enum astrolabe_route route, astrolabe_lease_t lease);
bool astrolabe_route_is_current(enum astrolabe_route route, astrolabe_lease_t lease);
enum astrolabe_route astrolabe_route_owner(void);
bool astrolabe_route_forced_standalone(void);
/* Send the full control state to the owner, e.g. once it has subscribed to receive it. */
int astrolabe_route_publish_snapshot(enum astrolabe_route route, astrolabe_lease_t lease);

/* The standalone HID mouse. Driven only by the route. */
struct astrolabe_mouse_config {
    float cursor_gain;    /* report units per radian */
    float scroll_gain;    /* scroll accumulator units per radian */
    float scroll_divisor; /* accumulator units per wheel detent */
    float yaw_deadzone;   /* radians per window */
    float yaw_dominance;  /* how far twist must outweigh tilt to read as scroll */
    uint32_t scroll_hold_ms;
    uint32_t retry_ms; /* delay before re-sending after the endpoint refused a report */
};

void astrolabe_mouse_init(const struct astrolabe_mouse_config *config);
/* One delivery window's worth of rotation, in radians. */
void astrolabe_mouse_motion(const float rotation[3], uint32_t now_ms);
void astrolabe_mouse_buttons(uint8_t press, uint8_t release);
/* Disabling releases any held button synchronously, before returning, so a claim cannot strand a
 * click at the host. */
void astrolabe_mouse_set_enabled(bool enabled);

#if IS_ENABLED(CONFIG_ASTROLABE_INDICATOR)
/* `applied` false: the toggle could not take effect and `transport` is still the one in use. */
void astrolabe_indicator_show_transport(enum zmk_transport transport, bool applied);
#else
static inline void astrolabe_indicator_show_transport(enum zmk_transport transport, bool applied) {
    (void)transport;
    (void)applied;
}
#endif
