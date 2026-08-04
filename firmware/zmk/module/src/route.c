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
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include <zmk/endpoints.h>
#include <zmk/hid.h>
#include <zmk/keymap.h>

LOG_MODULE_REGISTER(astrolabe_route, CONFIG_ASTROLABE_LOG_LEVEL);

#define ROUTE_NODE DT_NODELABEL(astrolabe_route)
/* Standalone output runs on its own thread so a stalled HID endpoint cannot reach the sensor poll
 * loop. ZMK's endpoint send waits on a binary semaphore with a 30 ms timeout; doing that inline in
 * the poll handler stalls sampling, the sensors keep integrating, and the backlog later releases
 * as a burst. The daemon routes send inline from the poll handler instead -- their transport call
 * does not block -- which is why only this path needs a thread.
 *
 * Priority 0 is the highest preemptible priority. Note that no preemptible priority can help if
 * the system workqueue saturates: it is cooperative
 * (CONFIG_SYSTEM_WORKQUEUE_PRIORITY=-1) and k_yield() only yields to equal-or-higher priority, so
 * a busy queue starves this thread outright. Keeping the poll chain paced rather than
 * interrupt-driven is what makes that not happen; see motion_interrupt() in the PMW3610 driver. */
#define OUTPUT_STACK_SIZE 1024
#define OUTPUT_THREAD_PRIORITY 0

struct route_state {
    struct k_mutex lock;
    struct k_mutex output_lock;
    uint8_t sent_buttons;
    uint8_t button_latch;
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

static int astrolabe_transport_rotation(enum astrolabe_route route, const uint8_t payload[12]) {
    switch (route) {
    case ASTROLABE_ROUTE_BLE_DAEMON:
        return astrolabe_gatt_rotation(payload);
    case ASTROLABE_ROUTE_USB_DAEMON:
        return astrolabe_usb_rotation(payload);
    default:
        return -ENOTSUP;
    }
}

static int astrolabe_transport_snapshot(enum astrolabe_route route, const uint8_t payload[6]) {
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

static K_THREAD_STACK_DEFINE(output_stack, OUTPUT_STACK_SIZE);
static struct k_work_q output_queue;
static struct k_work output_work;

static void submit_output(void);

/* The route is the sole authority over the daemon layer, and the coupling is deliberately one
 * way: a route change may move the layer, but a layer change never moves the route. The route
 * can transition with no user action at all -- cable pull, daemon crash, keepalive timeout -- so
 * the layer has to be able to snap back on its own. Two writers would drift apart with no
 * defined way to reconcile.
 *
 * Called with no route lock held: zmk_keymap_layer_* raises a layer-state event synchronously,
 * and running listeners under the lock would invert the ordering the output path relies on. */
static void apply_route_layer(enum astrolabe_route route) {
    if (route == ASTROLABE_ROUTE_STANDALONE) {
        zmk_keymap_layer_deactivate(CONFIG_ASTROLABE_DAEMON_LAYER);
    } else {
        zmk_keymap_layer_activate(CONFIG_ASTROLABE_DAEMON_LAYER);
    }
}

static int16_t clamp_report(float value) {
    if (value > 32767.0f) {
        return 32767;
    }
    if (value < -32767.0f) {
        return -32767;
    }
    return (int16_t)value;
}

/* One writer of ZMK's mouse report. Buttons are re-asserted from the desired mask every cycle
 * rather than tracked as edges, so a failed send self-heals on the next one.
 *
 * Movement is a level and collapsing it across cycles is correct; a button is an edge and
 * collapsing it loses the event outright. A press and its release can easily land in the same
 * cycle -- ZMK's combo layer captures a candidate press and replays it only once the combo
 * times out, so the release arrives immediately behind it -- and a second submit while this work
 * is still pending is a no-op. Without the latch the handler would then see the mask back at
 * sent_buttons and send nothing at all, discarding the click rather than shortening it. Latched
 * bits are held until they have actually gone out, and the release follows on the next cycle. */
static void output_handler(struct k_work *work) {
    ARG_UNUSED(work);

    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized || state.current != ASTROLABE_ROUTE_STANDALONE) {
        k_mutex_unlock(&state.lock);
        return;
    }
    const astrolabe_route_lease_t epoch = state.epoch;
    const int16_t dx = clamp_report(state.cursor_x);
    const int16_t dy = clamp_report(state.cursor_y);
    const int16_t wheel = clamp_report(state.scroll / state.config.scroll_divisor);
    const uint8_t latched = state.button_latch;
    const uint8_t buttons = state.standalone_buttons | latched;
    const bool buttons_changed = buttons != state.sent_buttons;
    k_mutex_unlock(&state.lock);

    if (dx == 0 && dy == 0 && wheel == 0 && !buttons_changed) {
        return;
    }

    for (uint8_t button = 0; button < 5U; ++button) {
        if ((buttons & BIT(button)) != 0U) {
            zmk_hid_mouse_button_press(button);
        } else {
            zmk_hid_mouse_button_release(button);
        }
    }
    zmk_hid_mouse_movement_set(dx, dy);
    zmk_hid_mouse_scroll_set(0, wheel);
    const int err = zmk_endpoints_send_mouse_report();
    zmk_hid_mouse_movement_set(0, 0);
    zmk_hid_mouse_scroll_set(0, 0);
    if (err != 0) {
        /* Retire nothing. Unlike ZMK's input listener, which zeroes its pending movement whether
         * or not the send succeeded, the motion stays in the accumulator and the next cycle
         * carries it. Delivery may be late; it is never silently dropped. */
        return;
    }

    bool more = false;
    k_mutex_lock(&state.lock, K_FOREVER);
    if (state.current == ASTROLABE_ROUTE_STANDALONE && state.epoch == epoch) {
        state.cursor_x -= (float)dx;
        state.cursor_y -= (float)dy;
        state.scroll -= (float)wheel * state.config.scroll_divisor;
        state.sent_buttons = buttons;
        state.button_latch &= (uint8_t)~latched;
        more = state.standalone_buttons != buttons;
    }
    k_mutex_unlock(&state.lock);

    /* A latched button that is already released still owes the host its release. */
    if (more) {
        submit_output();
    }
}

static void submit_output(void) { k_work_submit_to_queue(&output_queue, &output_work); }

int astrolabe_route_init(const struct astrolabe_route_config *config) {
    if (config == NULL || config->radius_counts <= 0.0f || config->scroll_divisor <= 0.0f) {
        return -EINVAL;
    }

    static const struct k_work_queue_config output_queue_config = {.name = "astrolabe_out"};
    k_work_queue_init(&output_queue);
    k_work_queue_start(&output_queue, output_stack, K_THREAD_STACK_SIZEOF(output_stack),
                       OUTPUT_THREAD_PRIORITY, &output_queue_config);
    k_work_init(&output_work, output_handler);

    k_mutex_init(&state.lock);
    k_mutex_init(&state.output_lock);
    k_mutex_lock(&state.lock, K_FOREVER);
    state.sent_buttons = 0U;
    state.config = *config;
    state.current = ASTROLABE_ROUTE_STANDALONE;
    state.controls = 0U;
    state.suppressed_controls = 0U;
    state.standalone_buttons = 0U;
    state.button_latch = 0U;
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
        /* Dropped, not sent: a latched press the host never saw needs no release. */
        state.button_latch = 0U;
    }
    k_mutex_unlock(&state.lock);

    /* Revoke the old transport and drain standalone buttons before publishing. */
    revoke_transport(previous, previous_lease);
    if (released_buttons != 0U) {
        submit_output();
    }

    k_mutex_lock(&state.lock, K_FOREVER);
    state.current = route;
    *lease = next_epoch_locked();
    clear_motion_locked();
    k_mutex_unlock(&state.lock);
    k_mutex_unlock(&state.output_lock);
    apply_route_layer(route);
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
    state.button_latch = 0U;
    clear_motion_locked();
    k_mutex_unlock(&state.lock);
    k_mutex_unlock(&state.output_lock);
    apply_route_layer(ASTROLABE_ROUTE_STANDALONE);

    /* A lost USB attach ACK can leave the daemon's BLE link subscribed but unowned. */
    if (route == ASTROLABE_ROUTE_USB_DAEMON) {
        astrolabe_gatt_route_available();
    }
}

void astrolabe_route_motion(float wx, float wy, float wz, uint32_t now_ms) {
    k_mutex_lock(&state.lock, K_FOREVER);
    if (!state.initialized) {
        k_mutex_unlock(&state.lock);
        return;
    }

    /* Radians, for every route. Solved counts scale with ball diameter and sensor CPI, so a
     * threshold expressed in them silently retunes itself whenever either changes; radians are the
     * physical quantity the gesture actually has. This is also what makes the standalone constants
     * directly comparable to the daemon's tuned values, which are already in radians -- the two
     * classify the same gesture over the same window and should not disagree. */
    state.rotation[0] += wx / state.config.radius_counts;
    state.rotation[1] += wy / state.config.radius_counts;
    state.rotation[2] += wz / state.config.radius_counts;

#if CONFIG_ASTROLABE_LOG_LEVEL >= 4
    /* Per-sample yaw contamination: how far an individual poll's solved rotation is from the
     * accumulated gesture it belongs to. A single-sensor poll is underdetermined, so this is
     * expected to be large; it is logged to confirm the errors cancel across the window rather
     * than to justify filtering any sample. */
    static uint32_t window_start_ms;
    static uint32_t window_samples;
    static uint32_t window_yaw_dominant;
    ++window_samples;
    if (fabsf(wz) > state.config.yaw_dominance * sqrtf(wx * wx + wy * wy)) {
        ++window_yaw_dominant;
    }
    if ((uint32_t)(now_ms - window_start_ms) >= 500U) {
        LOG_DBG("motion: %u samples/%ums yaw_dominant=%u accum mrad x=%d y=%d z=%d", window_samples,
                (unsigned)(now_ms - window_start_ms), window_yaw_dominant,
                (int)(state.rotation[0] * 1000.0f), (int)(state.rotation[1] * 1000.0f),
                (int)(state.rotation[2] * 1000.0f));
        window_start_ms = now_ms;
        window_samples = 0U;
        window_yaw_dominant = 0U;
    }
#endif
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
        /* The window closes here whether or not it produced a report. Classification latches
         * (scroll_hold_ms), so pacing it off successful delivery is a trap: with nothing pending
         * the gate above passes on every poll, classification degenerates to the single-sample
         * case, and a latched false positive then suppresses the cursor that would have cleared
         * it. Window pacing is about how much gesture to judge at once, not about delivery. */
        state.last_send_ms = now_ms;

        /* Classify the accumulated gesture, not one sample of it. A poll where only one sensor
         * asserted MOTION is underdetermined, and the solver answers it with a large spurious yaw
         * whose sign follows whichever sensor reported. Those errors are equal and opposite
         * between left-only and right-only samples, so they cancel once summed -- but only if
         * nothing branches on an individual sample first. */
        const float wx = state.rotation[0];
        const float wy = state.rotation[1];
        const float wz = state.rotation[2];
        if (wx != 0.0f || wy != 0.0f || wz != 0.0f) {
            const float yaw = fabsf(wz);
            const float horizontal = sqrtf(wx * wx + wy * wy);
            const bool scroll_now =
                yaw > state.config.yaw_deadzone && yaw > state.config.yaw_dominance * horizontal;
            if (scroll_now) {
                state.last_scroll_ms = now_ms;
            }
            bool scrolling = scroll_now || (uint32_t)(now_ms - state.last_scroll_ms) <
                                               state.config.scroll_hold_ms;
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
            memset(state.rotation, 0, sizeof(state.rotation));
        }

        const bool pending = clamp_report(state.cursor_x) != 0 ||
                             clamp_report(state.cursor_y) != 0 ||
                             clamp_report(state.scroll / state.config.scroll_divisor) != 0 ||
                             state.standalone_buttons != state.sent_buttons;
        k_mutex_unlock(&state.lock);
        if (pending) {
            submit_output();
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
            state.button_latch |= button_change;
        } else if (!pressed) {
            button_change = standalone_buttons & state.standalone_buttons;
            state.standalone_buttons &= ~standalone_buttons;
        }
    } else {
        publish_snapshot = true;
    }
    k_mutex_unlock(&state.lock);

    if (button_change != 0U) {
        /* The output thread re-asserts the whole desired mask, so it only needs waking. */
        submit_output();
    }
    if (publish_snapshot) {
        astrolabe_route_publish_snapshot(route, epoch);
    }
}

