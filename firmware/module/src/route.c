/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * The route: who owns motion and controls right now, and delivery to that owner.
 *
 * Standalone is the resting state; the device is an ordinary HID mouse (mouse.c). A daemon claims
 * the route over BLE or USB and receives raw rotation and control snapshots instead, until it
 * releases, disconnects, or stops sending keepalives -- at which point the route falls back to
 * standalone on its own. The route is also the only writer of the keymap's daemon layer, so the
 * layer can never disagree with who actually owns input.
 */

#define DT_DRV_COMPAT astrolabe_controller

#include <astrolabe/astrolabe.h>

#include "core.h"

#include <errno.h>
#include <string.h>

#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include <zmk/keymap.h>

LOG_MODULE_REGISTER(astrolabe_route, CONFIG_ASTROLABE_LOG_LEVEL);

BUILD_ASSERT(DT_NUM_INST_STATUS_OKAY(DT_DRV_COMPAT) == 1,
             "the shield must declare exactly one astrolabe,controller node");

#define CONTROLLER DT_DRV_INST(0)
#define SEND_INTERVAL_MS DT_PROP(CONTROLLER, send_interval_ms)
/* How long after the last motion a failed delivery keeps being retried. Past it, the remainder
 * waits for the next motion rather than polling a transport that is not there. */
#define RETRY_WINDOW_MS 2000U

static const struct gpio_dt_spec force_standalone_pin =
    GPIO_DT_SPEC_GET_OR(CONTROLLER, force_standalone_gpios, {0});
/* Standalone HID button mask for each control bit; zero means the control has no click. */
static const uint8_t control_buttons[] = DT_PROP_OR(CONTROLLER, control_buttons, {0});

static const struct astrolabe_mouse_config mouse_config = {
    .cursor_gain = DT_PROP(CONTROLLER, cursor_gain_milli) / 1000.0f,
    .scroll_gain = DT_PROP(CONTROLLER, scroll_gain_milli) / 1000.0f,
    .scroll_divisor = DT_PROP(CONTROLLER, scroll_divisor),
    .yaw_deadzone = DT_PROP(CONTROLLER, yaw_deadzone_milli) / 1000.0f,
    .yaw_dominance = DT_PROP(CONTROLLER, yaw_dominance_milli) / 1000.0f,
    .scroll_hold_ms = DT_PROP(CONTROLLER, scroll_hold_ms),
    .retry_ms = SEND_INTERVAL_MS,
};

/* Lock order: send_lock, then lock. send_lock orders deliveries against ownership changes, so a
 * packet built for one owner is never handed to the next. Neither is held across a keymap call:
 * layer changes raise events synchronously. */
static K_MUTEX_DEFINE(send_lock);
static K_MUTEX_DEFINE(lock);

static struct {
    const struct astrolabe_transport *transports[ASTROLABE_ROUTE_COUNT];
    void (*wake)(void);
    enum astrolabe_route owner;
    astrolabe_lease_t lease;
    uint8_t controls;
    /* Controls held when a daemon let go. They stay silent until released, so the handover never
     * turns a press the daemon consumed into a standalone click. */
    uint8_t suppressed;
    uint16_t sequence;
    float rotation[3];
    uint32_t last_flush_ms;
    uint32_t last_motion_ms;
    bool forced_standalone;
} route = {
    .owner = ASTROLABE_ROUTE_STANDALONE,
    .lease = 1U,
};

static void flush(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(flush_work, flush);

static astrolabe_lease_t next_lease_locked(void) {
    if (++route.lease == ASTROLABE_LEASE_NONE) {
        ++route.lease;
    }
    return route.lease;
}

static void apply_layer(enum astrolabe_route owner) {
    if (owner == ASTROLABE_ROUTE_STANDALONE) {
        zmk_keymap_layer_deactivate(CONFIG_ASTROLABE_DAEMON_LAYER);
    } else {
        zmk_keymap_layer_activate(CONFIG_ASTROLABE_DAEMON_LAYER);
    }
}

void astrolabe_route_register(enum astrolabe_route owner, const struct astrolabe_transport *api) {
    __ASSERT_NO_MSG(owner > ASTROLABE_ROUTE_STANDALONE && owner < ASTROLABE_ROUTE_COUNT);
    k_mutex_lock(&lock, K_FOREVER);
    route.transports[owner] = api;
    k_mutex_unlock(&lock);
}

void astrolabe_register_source(void (*wake)(void)) {
    k_mutex_lock(&lock, K_FOREVER);
    route.wake = wake;
    k_mutex_unlock(&lock);
}

enum astrolabe_route astrolabe_route_owner(void) {
    k_mutex_lock(&lock, K_FOREVER);
    const enum astrolabe_route owner = route.owner;
    k_mutex_unlock(&lock);
    return owner;
}

bool astrolabe_route_forced_standalone(void) { return route.forced_standalone; }

bool astrolabe_route_is_current(enum astrolabe_route owner, astrolabe_lease_t lease) {
    k_mutex_lock(&lock, K_FOREVER);
    const bool current = owner != ASTROLABE_ROUTE_STANDALONE && route.owner == owner &&
                         lease != ASTROLABE_LEASE_NONE && route.lease == lease;
    k_mutex_unlock(&lock);
    return current;
}

int astrolabe_route_claim(enum astrolabe_route owner, astrolabe_lease_t *lease) {
    if (owner == ASTROLABE_ROUTE_STANDALONE || owner >= ASTROLABE_ROUTE_COUNT || lease == NULL) {
        return -EINVAL;
    }
    if (route.forced_standalone) {
        return -EPERM;
    }

    k_mutex_lock(&send_lock, K_FOREVER);
    k_mutex_lock(&lock, K_FOREVER);
    /* USB wins: a wired daemon that is mid-handover must not lose the route to its own BLE link. */
    if (owner == ASTROLABE_ROUTE_BLE && route.owner == ASTROLABE_ROUTE_USB) {
        k_mutex_unlock(&lock);
        k_mutex_unlock(&send_lock);
        return -EBUSY;
    }
    const enum astrolabe_route previous = route.owner;
    const astrolabe_lease_t previous_lease = route.lease;
    const struct astrolabe_transport *previous_api = route.transports[previous];
    k_mutex_unlock(&lock);

    if (previous == ASTROLABE_ROUTE_STANDALONE) {
        /* Synchronous: once the route moves, standalone output is suppressed for good, so a
         * release left queued behind the change would leave the host holding a click. */
        astrolabe_mouse_set_enabled(false);
    } else if (previous_api != NULL) {
        previous_api->revoked(previous_lease);
    }

    k_mutex_lock(&lock, K_FOREVER);
    route.owner = owner;
    *lease = next_lease_locked();
    memset(route.rotation, 0, sizeof(route.rotation));
    k_mutex_unlock(&lock);
    k_mutex_unlock(&send_lock);

    apply_layer(owner);
    LOG_INF("route claimed by %s", owner == ASTROLABE_ROUTE_USB ? "USB" : "BLE");
    return 0;
}

void astrolabe_route_release(enum astrolabe_route owner, astrolabe_lease_t lease) {
    k_mutex_lock(&send_lock, K_FOREVER);
    if (!astrolabe_route_is_current(owner, lease)) {
        k_mutex_unlock(&send_lock);
        return;
    }
    k_mutex_lock(&lock, K_FOREVER);
    const struct astrolabe_transport *api = route.transports[owner];
    k_mutex_unlock(&lock);

    if (api != NULL) {
        api->revoked(lease);
    }

    k_mutex_lock(&lock, K_FOREVER);
    route.owner = ASTROLABE_ROUTE_STANDALONE;
    (void)next_lease_locked();
    route.suppressed = route.controls;
    memset(route.rotation, 0, sizeof(route.rotation));
    k_mutex_unlock(&lock);
    astrolabe_mouse_set_enabled(true);
    k_mutex_unlock(&send_lock);

    apply_layer(ASTROLABE_ROUTE_STANDALONE);
    LOG_INF("route released to standalone");
}

int astrolabe_route_publish_snapshot(enum astrolabe_route owner, astrolabe_lease_t lease) {
    uint8_t packet[ASTROLABE_SNAPSHOT_BYTES];

    k_mutex_lock(&send_lock, K_FOREVER);
    k_mutex_lock(&lock, K_FOREVER);
    const struct astrolabe_transport *api = route.transports[owner];
    if (owner == ASTROLABE_ROUTE_STANDALONE || route.owner != owner || route.lease != lease ||
        api == NULL) {
        k_mutex_unlock(&lock);
        k_mutex_unlock(&send_lock);
        return -EACCES;
    }
    astrolabe_encode_snapshot(route.sequence, route.controls, packet);
    k_mutex_unlock(&lock);
    const int err = api->send_snapshot(packet);
    k_mutex_unlock(&send_lock);
    return err;
}

void astrolabe_report_motion(float rx, float ry, float rz) {
    k_mutex_lock(&lock, K_FOREVER);
    route.rotation[0] += rx;
    route.rotation[1] += ry;
    route.rotation[2] += rz;
    const uint32_t now = k_uptime_get_32();
    const uint32_t elapsed = now - route.last_flush_ms;
    route.last_motion_ms = now;
    k_mutex_unlock(&lock);

    /* Deliver at most once per send interval, measured from the previous delivery, and no later.
     * k_work_schedule leaves an already-scheduled delivery alone, so the interval is a fixed beat
     * rather than something every report pushes back. */
    k_work_schedule(&flush_work,
                    elapsed >= SEND_INTERVAL_MS ? K_NO_WAIT : K_MSEC(SEND_INTERVAL_MS - elapsed));
}

/*
 * One delivery window. For standalone, the window closes here whether or not it produced a
 * report: the mouse classifies each window as cursor or scroll, and pacing windows off delivery
 * lets classification degenerate to single samples, where a latched false positive suppresses the
 * very motion that would clear it.
 */
static void flush(struct k_work *work) {
    ARG_UNUSED(work);

    k_mutex_lock(&send_lock, K_FOREVER);
    k_mutex_lock(&lock, K_FOREVER);
    const uint32_t now = k_uptime_get_32();
    const enum astrolabe_route owner = route.owner;
    const astrolabe_lease_t lease = route.lease;
    const struct astrolabe_transport *api = route.transports[owner];
    float window[3];
    memcpy(window, route.rotation, sizeof(window));
    const bool moved = window[0] != 0.0f || window[1] != 0.0f || window[2] != 0.0f;
    if (owner == ASTROLABE_ROUTE_STANDALONE) {
        memset(route.rotation, 0, sizeof(route.rotation));
        route.last_flush_ms = now;
    } else if (moved) {
        route.last_flush_ms = now;
    }
    const bool retry_allowed = (uint32_t)(now - route.last_motion_ms) < RETRY_WINDOW_MS;
    k_mutex_unlock(&lock);

    if (owner == ASTROLABE_ROUTE_STANDALONE) {
        astrolabe_mouse_motion(window, now);
        k_mutex_unlock(&send_lock);
        return;
    }
    if (!moved) {
        k_mutex_unlock(&send_lock);
        return;
    }

    uint8_t packet[ASTROLABE_ROTATION_BYTES];
    astrolabe_encode_rotation(window, packet);
    const int err = api != NULL ? api->send_rotation(packet) : -ENOTSUP;

    /* Retire only what was delivered. A failed send leaves the motion accumulated, so delivery may
     * be late but is never silently dropped. */
    k_mutex_lock(&lock, K_FOREVER);
    if (err == 0 && route.owner == owner && route.lease == lease) {
        for (int i = 0; i < 3; ++i) {
            route.rotation[i] -= window[i];
        }
    }
    k_mutex_unlock(&lock);
    k_mutex_unlock(&send_lock);

    if (err != 0 && retry_allowed) {
        k_work_schedule(&flush_work, K_MSEC(SEND_INTERVAL_MS));
    }
}

void astrolabe_report_control(uint8_t bit, bool pressed) {
    k_mutex_lock(&lock, K_FOREVER);
    void (*wake)(void) = route.wake;
    k_mutex_unlock(&lock);
    if (wake != NULL) {
        wake();
    }
    if (bit >= ASTROLABE_MAX_CONTROLS) {
        return;
    }

    const uint8_t mask = BIT(bit);
    const uint8_t buttons = bit < ARRAY_SIZE(control_buttons) ? control_buttons[bit] : 0U;
    uint8_t press = 0U;
    uint8_t release = 0U;

    k_mutex_lock(&lock, K_FOREVER);
    if (((route.controls & mask) != 0U) == pressed) {
        k_mutex_unlock(&lock);
        return;
    }
    WRITE_BIT(route.controls, bit, pressed);
    ++route.sequence;
    if (!pressed) {
        route.suppressed &= ~mask;
        release = buttons;
    } else if ((route.suppressed & mask) == 0U) {
        press = buttons;
    }
    const enum astrolabe_route owner = route.owner;
    const astrolabe_lease_t lease = route.lease;
    k_mutex_unlock(&lock);

    if (owner != ASTROLABE_ROUTE_STANDALONE) {
        (void)astrolabe_route_publish_snapshot(owner, lease);
    } else if ((press | release) != 0U) {
        astrolabe_mouse_buttons(press, release);
    }
}

/* Runs at POST_KERNEL/40, before kscan (POST_KERNEL/90) configures the same pin: holding Center
 * through boot is the forced-standalone escape, and kscan must be the one that ends up owning it. */
static int route_init(void) {
    if (gpio_is_ready_dt(&force_standalone_pin) &&
        gpio_pin_configure_dt(&force_standalone_pin, GPIO_INPUT) == 0) {
        route.forced_standalone = gpio_pin_get_dt(&force_standalone_pin) > 0;
    }
    if (route.forced_standalone) {
        LOG_WRN("forced standalone: daemon claims are refused until reboot");
    }
    astrolabe_mouse_init(&mouse_config);
    return 0;
}

SYS_INIT(route_init, POST_KERNEL, CONFIG_KERNEL_INIT_PRIORITY_DEFAULT);
