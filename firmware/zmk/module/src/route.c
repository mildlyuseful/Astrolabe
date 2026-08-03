/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#include <astrolabe/protocol.h>
#include <astrolabe/route.h>
#include <astrolabe/transport.h>

#include <errno.h>
#include <math.h>
#include <string.h>

#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/init.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

LOG_MODULE_REGISTER(astrolabe_route, CONFIG_ASTROLABE_LOG_LEVEL);

#define ROUTE_NODE DT_NODELABEL(astrolabe_route)
#define HID_DRAIN_MAX 4U

struct route_state {
    struct k_mutex lock;
    struct k_mutex output_lock;
    const struct device *pointer_device;
    struct astrolabe_route_config config;
    enum astrolabe_route current;
    uint8_t controls;
    uint8_t suppressed_controls;
    uint8_t standalone_buttons;
    uint16_t sequence;
    float cursor_x;
    float cursor_y;
    float scroll;
    float rotation[3];
    uint32_t last_scroll_ms;
    uint32_t last_send_ms;
    astrolabe_route_lease_t epoch;
    bool initialized;
    bool forced_standalone;
};

static struct route_state state;

static const struct gpio_dt_spec force_standalone =
    GPIO_DT_SPEC_GET(ROUTE_NODE, force_standalone_gpios);

__weak int astrolabe_gatt_rotation(const uint8_t payload[12]) {
    ARG_UNUSED(payload);
    return -ENOTSUP;
}

__weak int astrolabe_gatt_snapshot(const uint8_t payload[6]) {
    ARG_UNUSED(payload);
    return -ENOTSUP;
}

__weak int astrolabe_usb_rotation(const uint8_t payload[12]) {
    ARG_UNUSED(payload);
    return -ENOTSUP;
}

__weak int astrolabe_usb_snapshot(const uint8_t payload[6]) {
    ARG_UNUSED(payload);
    return -ENOTSUP;
}

__weak void astrolabe_gatt_route_revoked(astrolabe_route_lease_t lease) { ARG_UNUSED(lease); }

__weak void astrolabe_usb_route_revoked(astrolabe_route_lease_t lease) { ARG_UNUSED(lease); }

__weak void astrolabe_gatt_route_available(void) {}

int astrolabe_transport_rotation(enum astrolabe_route route, const uint8_t payload[12]) {
    switch (route) {
    case ASTROLABE_ROUTE_BLE_DAEMON:
        return astrolabe_gatt_rotation(payload);
    case ASTROLABE_ROUTE_USB_DAEMON:
        return astrolabe_usb_rotation(payload);
    default:
        return -ENOTSUP;
    }
}

int astrolabe_transport_snapshot(enum astrolabe_route route, const uint8_t payload[6]) {
    switch (route) {
    case ASTROLABE_ROUTE_BLE_DAEMON:
        return astrolabe_gatt_snapshot(payload);
    case ASTROLABE_ROUTE_USB_DAEMON:
        return astrolabe_usb_snapshot(payload);
    default:
        return -ENOTSUP;
    }
}

static int sample_forced_standalone(void) {
    if (!gpio_is_ready_dt(&force_standalone)) {
        return -ENODEV;
    }

    int err = gpio_pin_configure_dt(&force_standalone, GPIO_INPUT);
    if (err != 0) {
        return err;
    }

    const int value = gpio_pin_get_dt(&force_standalone);
    if (value < 0) {
        return value;
    }
    state.forced_standalone = value != 0;
    return 0;
}

SYS_INIT(sample_forced_standalone, POST_KERNEL, CONFIG_KERNEL_INIT_PRIORITY_DEFAULT);

static void clear_motion_locked(void) {
    state.cursor_x = 0.0f;
    state.cursor_y = 0.0f;
    state.scroll = 0.0f;
    memset(state.rotation, 0, sizeof(state.rotation));
}

static astrolabe_route_lease_t next_epoch_locked(void) {
    ++state.epoch;
    if (state.epoch == ASTROLABE_ROUTE_LEASE_NONE) {
        ++state.epoch;
    }
    return state.epoch;
}

static void revoke_transport(enum astrolabe_route route, astrolabe_route_lease_t lease) {
    if (lease == ASTROLABE_ROUTE_LEASE_NONE) {
        return;
    }
    switch (route) {
    case ASTROLABE_ROUTE_BLE_DAEMON:
        astrolabe_gatt_route_revoked(lease);
        break;
    case ASTROLABE_ROUTE_USB_DAEMON:
        astrolabe_usb_route_revoked(lease);
        break;
    default:
        break;
    }
}

static int report_rel(uint16_t code, int32_t value, bool sync) {
    if (value == 0) {
        return 0;
    }
    return input_report_rel(state.pointer_device, code, value, sync, K_FOREVER);
}

static int report_button_mask(uint8_t mask, bool pressed) {
    int err = 0;
    for (uint8_t button = 0; button < 5U; ++button) {
        if ((mask & BIT(button)) == 0U) {
            continue;
        }
        const bool sync = (mask & ~GENMASK(button, 0)) == 0U;
        const int next =
            input_report_key(state.pointer_device, INPUT_BTN_0 + button, pressed, sync, K_FOREVER);
        if (err == 0 && next != 0) {
            err = next;
        }
    }
    return err;
}

static int clamp_hid(float value) {
    if (value > 127.0f) {
        return 127;
    }
    if (value < -127.0f) {
        return -127;
    }
    return (int)value;
}

int astrolabe_route_init(const struct device *pointer_device,
                         const struct astrolabe_route_config *config) {
    if (pointer_device == NULL || config == NULL || config->radius_counts <= 0.0f ||
        config->scroll_divisor <= 0.0f) {
        return -EINVAL;
    }

    k_mutex_init(&state.lock);
    k_mutex_init(&state.output_lock);
    k_mutex_lock(&state.lock, K_FOREVER);
    state.pointer_device = pointer_device;
    state.config = *config;
    state.current = ASTROLABE_ROUTE_STANDALONE;
    state.controls = 0U;
    state.suppressed_controls = 0U;
    state.standalone_buttons = 0U;
    state.sequence = 0U;
    clear_motion_locked();
    state.last_scroll_ms = 0U;
    state.last_send_ms = 0U;
    state.epoch = 1U;
    state.initialized = true;
    k_mutex_unlock(&state.lock);
    return 0;
}

enum astrolabe_route astrolabe_route_current(void) {
    k_mutex_lock(&state.lock, K_FOREVER);
    const enum astrolabe_route current = state.current;
    k_mutex_unlock(&state.lock);
    return current;
}

bool astrolabe_route_forced_standalone(void) { return state.forced_standalone; }

int astrolabe_route_publish_snapshot(enum astrolabe_route route, astrolabe_route_lease_t lease) {
    uint8_t packet[ASTROLABE_INPUT_PACKET_BYTES];

    k_mutex_lock(&state.output_lock, K_FOREVER);
    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized || state.current != route || state.epoch != lease ||
        route == ASTROLABE_ROUTE_STANDALONE) {
        k_mutex_unlock(&state.lock);
        k_mutex_unlock(&state.output_lock);
        return -EACCES;
    }
    astrolabe_encode_input_snapshot(state.sequence, state.controls, packet);
    k_mutex_unlock(&state.lock);
    const int err = astrolabe_transport_snapshot(route, packet);
    k_mutex_unlock(&state.output_lock);
    return err;
}

int astrolabe_route_claim(enum astrolabe_route route, astrolabe_route_lease_t *lease) {
    if (route == ASTROLABE_ROUTE_STANDALONE || route > ASTROLABE_ROUTE_USB_DAEMON ||
        lease == NULL) {
        return -EINVAL;
    }
    if (state.forced_standalone) {
        return -EPERM;
    }

    uint8_t released_buttons = 0U;
    enum astrolabe_route previous;
    astrolabe_route_lease_t previous_lease;

    k_mutex_lock(&state.output_lock, K_FOREVER);
    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized) {
        k_mutex_unlock(&state.lock);
        k_mutex_unlock(&state.output_lock);
        return -EAGAIN;
    }
    /* USB wins the daemon's ACK-before-BLE handover window. */
    if (route == ASTROLABE_ROUTE_BLE_DAEMON && state.current == ASTROLABE_ROUTE_USB_DAEMON) {
        k_mutex_unlock(&state.lock);
        k_mutex_unlock(&state.output_lock);
        return -EBUSY;
    }

    previous = state.current;
    previous_lease =
        previous == ASTROLABE_ROUTE_STANDALONE ? ASTROLABE_ROUTE_LEASE_NONE : state.epoch;
    if (state.current == ASTROLABE_ROUTE_STANDALONE) {
        released_buttons = state.standalone_buttons;
        state.standalone_buttons = 0U;
    }
    k_mutex_unlock(&state.lock);

    /* Revoke the old transport and drain standalone buttons before publishing. */
    revoke_transport(previous, previous_lease);
    if (released_buttons != 0U) {
        report_button_mask(released_buttons, false);
    }

    k_mutex_lock(&state.lock, K_FOREVER);
    state.current = route;
    *lease = next_epoch_locked();
    clear_motion_locked();
    k_mutex_unlock(&state.lock);
    k_mutex_unlock(&state.output_lock);
    return 0;
}

bool astrolabe_route_lease_is_current(enum astrolabe_route route, astrolabe_route_lease_t lease) {
    k_mutex_lock(&state.lock, K_FOREVER);
    const bool current = state.initialized && route != ASTROLABE_ROUTE_STANDALONE &&
                         state.current == route && state.epoch == lease &&
                         lease != ASTROLABE_ROUTE_LEASE_NONE;
    k_mutex_unlock(&state.lock);
    return current;
}

void astrolabe_route_release(enum astrolabe_route route, astrolabe_route_lease_t lease) {
    k_mutex_lock(&state.output_lock, K_FOREVER);
    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized || state.current != route || state.epoch != lease ||
        lease == ASTROLABE_ROUTE_LEASE_NONE) {
        k_mutex_unlock(&state.lock);
        k_mutex_unlock(&state.output_lock);
        return;
    }
    k_mutex_unlock(&state.lock);

    revoke_transport(route, lease);

    k_mutex_lock(&state.lock, K_FOREVER);
    state.current = ASTROLABE_ROUTE_STANDALONE;
    (void)next_epoch_locked();
    state.suppressed_controls = state.controls;
    state.standalone_buttons = 0U;
    clear_motion_locked();
    k_mutex_unlock(&state.lock);
    k_mutex_unlock(&state.output_lock);

    /* A lost USB attach ACK can leave the daemon's BLE link subscribed but unowned. */
    if (route == ASTROLABE_ROUTE_USB_DAEMON) {
        astrolabe_gatt_route_available();
    }
}

void astrolabe_route_reset(void) {
    enum astrolabe_route previous;
    astrolabe_route_lease_t previous_lease;

    k_mutex_lock(&state.output_lock, K_FOREVER);
    k_mutex_lock(&state.lock, K_FOREVER);
    previous = state.current;
    previous_lease =
        previous == ASTROLABE_ROUTE_STANDALONE ? ASTROLABE_ROUTE_LEASE_NONE : state.epoch;
    k_mutex_unlock(&state.lock);
    revoke_transport(previous, previous_lease);
    k_mutex_lock(&state.lock, K_FOREVER);
    state.current = ASTROLABE_ROUTE_STANDALONE;
    (void)next_epoch_locked();
    state.controls = 0U;
    state.suppressed_controls = 0U;
    state.standalone_buttons = 0U;
    state.sequence = 0U;
    clear_motion_locked();
    k_mutex_unlock(&state.lock);
    k_mutex_unlock(&state.output_lock);
}

void astrolabe_route_motion(float wx, float wy, float wz, uint32_t now_ms) {
    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized) {
        k_mutex_unlock(&state.lock);
        return;
    }

    if (state.current != ASTROLABE_ROUTE_STANDALONE) {
        state.rotation[0] += wx / state.config.radius_counts;
        state.rotation[1] += wy / state.config.radius_counts;
        state.rotation[2] += wz / state.config.radius_counts;
        k_mutex_unlock(&state.lock);
        return;
    }

    const float yaw = fabsf(wz);
    const float horizontal = sqrtf(wx * wx + wy * wy);
    const bool scroll_now =
        yaw > state.config.yaw_deadzone && yaw > state.config.yaw_dominance * horizontal;
    if (scroll_now) {
        state.last_scroll_ms = now_ms;
    }
    bool scrolling =
        scroll_now || (uint32_t)(now_ms - state.last_scroll_ms) < state.config.scroll_hold_ms;
    if (!scroll_now && horizontal > state.config.yaw_deadzone &&
        horizontal > state.config.yaw_dominance * yaw) {
        scrolling = false;
    }

    if (scrolling) {
        state.scroll -= wz * state.config.scroll_gain;
    } else {
        state.cursor_x -= wy * state.config.cursor_gain;
        state.cursor_y -= wx * state.config.cursor_gain;
    }
    k_mutex_unlock(&state.lock);
}

void astrolabe_route_flush(uint32_t now_ms) {
    enum astrolabe_route route;
    astrolabe_route_lease_t epoch;
    float rotation[3];

    k_mutex_lock(&state.output_lock, K_FOREVER);
    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized ||
        (uint32_t)(now_ms - state.last_send_ms) < state.config.send_interval_ms) {
        k_mutex_unlock(&state.lock);
        k_mutex_unlock(&state.output_lock);
        return;
    }

    route = state.current;
    epoch = state.epoch;
    if (route == ASTROLABE_ROUTE_STANDALONE) {
        float cursor_x = state.cursor_x;
        float cursor_y = state.cursor_y;
        const int wheel = clamp_hid(state.scroll / state.config.scroll_divisor);
        const bool sendable = clamp_hid(cursor_x) != 0 || clamp_hid(cursor_y) != 0 || wheel != 0;
        if (sendable) {
            state.last_send_ms = now_ms;
        }
        k_mutex_unlock(&state.lock);

        for (uint8_t packet = 0; packet < HID_DRAIN_MAX; ++packet) {
            const int x = clamp_hid(cursor_x);
            const int y = clamp_hid(cursor_y);
            if (x == 0 && y == 0) {
                break;
            }
            int err = 0;
            if (x != 0) {
                err = report_rel(INPUT_REL_X, x, y == 0);
            }
            if (err == 0 && y != 0) {
                err = report_rel(INPUT_REL_Y, y, true);
            }
            if (err != 0) {
                break;
            }
            cursor_x -= (float)x;
            cursor_y -= (float)y;
            k_mutex_lock(&state.lock, K_FOREVER);
            if (state.current == ASTROLABE_ROUTE_STANDALONE && state.epoch == epoch) {
                state.cursor_x -= (float)x;
                state.cursor_y -= (float)y;
            }
            k_mutex_unlock(&state.lock);
        }

        if (wheel != 0 && report_rel(INPUT_REL_WHEEL, wheel, true) == 0) {
            k_mutex_lock(&state.lock, K_FOREVER);
            if (state.current == ASTROLABE_ROUTE_STANDALONE && state.epoch == epoch) {
                state.scroll -= (float)wheel * state.config.scroll_divisor;
            }
            k_mutex_unlock(&state.lock);
        }
        k_mutex_unlock(&state.output_lock);
        return;
    }

    memcpy(rotation, state.rotation, sizeof(rotation));
    if (rotation[0] == 0.0f && rotation[1] == 0.0f && rotation[2] == 0.0f) {
        k_mutex_unlock(&state.lock);
        k_mutex_unlock(&state.output_lock);
        return;
    }
    state.last_send_ms = now_ms;
    k_mutex_unlock(&state.lock);

    uint8_t packet[ASTROLABE_ROTATION_PACKET_BYTES];
    astrolabe_encode_rotation(-rotation[0], -rotation[1], -rotation[2], packet);
    if (astrolabe_transport_rotation(route, packet) != 0) {
        k_mutex_unlock(&state.output_lock);
        return;
    }

    k_mutex_lock(&state.lock, K_FOREVER);
    if (state.current == route && state.epoch == epoch) {
        state.rotation[0] -= rotation[0];
        state.rotation[1] -= rotation[1];
        state.rotation[2] -= rotation[2];
    }
    k_mutex_unlock(&state.lock);
    k_mutex_unlock(&state.output_lock);
}

void astrolabe_route_control(uint8_t bit_index, uint8_t standalone_buttons, bool pressed) {
    if (bit_index >= 5U || !state.initialized) {
        return;
    }

    const uint8_t control = BIT(bit_index);
    enum astrolabe_route route;
    astrolabe_route_lease_t epoch;
    uint8_t button_change = 0U;
    bool button_pressed = false;
    bool publish_snapshot = false;

    k_mutex_lock(&state.lock, K_FOREVER);
    const bool was_pressed = (state.controls & control) != 0U;
    if (was_pressed == pressed) {
        k_mutex_unlock(&state.lock);
        return;
    }

    WRITE_BIT(state.controls, bit_index, pressed);
    ++state.sequence;
    route = state.current;
    epoch = state.epoch;
    if (!pressed) {
        state.suppressed_controls &= ~control;
    }

    if (route == ASTROLABE_ROUTE_STANDALONE) {
        if (pressed && (state.suppressed_controls & control) == 0U) {
            button_change = standalone_buttons & ~state.standalone_buttons;
            state.standalone_buttons |= standalone_buttons;
            button_pressed = true;
        } else if (!pressed) {
            button_change = standalone_buttons & state.standalone_buttons;
            state.standalone_buttons &= ~standalone_buttons;
        }
    } else {
        publish_snapshot = true;
    }
    k_mutex_unlock(&state.lock);

    if (button_change != 0U) {
        k_mutex_lock(&state.output_lock, K_FOREVER);
        k_mutex_lock(&state.lock, K_FOREVER);
        const bool still_standalone =
            state.current == ASTROLABE_ROUTE_STANDALONE && state.epoch == epoch;
        k_mutex_unlock(&state.lock);
        if (still_standalone) {
            report_button_mask(button_change, button_pressed);
        }
        k_mutex_unlock(&state.output_lock);
    }
    if (publish_snapshot) {
        astrolabe_route_publish_snapshot(route, epoch);
    }
}

uint8_t astrolabe_route_controls(void) {
    k_mutex_lock(&state.lock, K_FOREVER);
    const uint8_t controls = state.controls;
    k_mutex_unlock(&state.lock);
    return controls;
}

uint16_t astrolabe_route_sequence(void) {
    k_mutex_lock(&state.lock, K_FOREVER);
    const uint16_t sequence = state.sequence;
    k_mutex_unlock(&state.lock);
    return sequence;
}
