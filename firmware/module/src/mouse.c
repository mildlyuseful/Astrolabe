/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * The standalone HID mouse: what the device is when no daemon owns the route.
 *
 * Ball tilt moves the cursor and twist scrolls. Each delivery window is classified as a whole, not
 * sample by sample: a poll where only one sensor saw motion is underdetermined and solves to a
 * large spurious twist whose sign follows whichever sensor reported. Those errors are equal and
 * opposite between sensors and cancel once summed, but only if nothing branches on a single sample.
 *
 * Output runs on its own thread so a stalled HID endpoint (ZMK's send can wait 30 ms) never reaches
 * the sensor poll; stalling the poll lets the sensors integrate a backlog that later lands as a lump.
 */

#include "core.h"

#include <math.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include <zmk/endpoints.h>
#include <zmk/hid.h>

LOG_MODULE_DECLARE(astrolabe_route, CONFIG_ASTROLABE_LOG_LEVEL);

#define OUTPUT_STACK_SIZE 1024
/* The highest preemptible priority. It cannot outrun a saturated system workqueue, which is
 * cooperative; keeping the sensor poll paced rather than edge-driven is what prevents that. */
#define OUTPUT_PRIORITY 0
#define BUTTON_COUNT 5U
/* A refused report is retried this long after the last input, then left for the next input. */
#define RETRY_WINDOW_MS 2000U

/* Lock order: send_lock, then lock. send_lock makes this file the one writer of ZMK's mouse
 * report, and covers applied_buttons. */
static K_MUTEX_DEFINE(send_lock);
static K_MUTEX_DEFINE(lock);

static struct astrolabe_mouse_config config;
static bool initialized;
static bool enabled = true;
/* Bumped whenever accumulated output stops meaning anything (route change), so a send that was in
 * flight across the change does not retire state that has since been reset. */
static uint32_t generation;
static float cursor_x;
static float cursor_y;
static float scroll;
static uint32_t last_scroll_ms;
static uint32_t last_input_ms;
static uint8_t buttons;      /* held now */
static uint8_t latch;        /* pressed but not yet sent */
static uint8_t sent_buttons; /* what the host last received */
static uint8_t applied_buttons;

static K_THREAD_STACK_DEFINE(output_stack, OUTPUT_STACK_SIZE);
static struct k_work_q output_queue;
static struct k_work_delayable output_work;

static int16_t clamp_report(float value) {
    return (int16_t)CLAMP(value, -32767.0f, 32767.0f);
}

static bool pending_locked(void) {
    return clamp_report(cursor_x) != 0 || clamp_report(cursor_y) != 0 ||
           clamp_report(scroll / config.scroll_divisor) != 0 || (buttons | latch) != sent_buttons;
}

static void submit(void) {
    k_work_reschedule_for_queue(&output_queue, &output_work, K_NO_WAIT);
}

/* ZMK's button press/release are reference counted, not idempotent: re-asserting a held button
 * every cycle builds a count the release then has to drain, which holds the click down for as long
 * as it was held. Drive only the edges against what ZMK has already been told. Caller holds
 * send_lock. */
static void apply_buttons(uint8_t mask) {
    const uint8_t changed = mask ^ applied_buttons;
    for (uint8_t button = 0; button < BUTTON_COUNT; ++button) {
        if ((changed & BIT(button)) == 0U) {
            continue;
        }
        if ((mask & BIT(button)) != 0U) {
            zmk_hid_mouse_button_press(button);
        } else {
            zmk_hid_mouse_button_release(button);
        }
    }
    applied_buttons = mask;
}

static int send_report(uint8_t mask, int16_t dx, int16_t dy, int16_t wheel) {
    apply_buttons(mask);
    zmk_hid_mouse_movement_set(dx, dy);
    zmk_hid_mouse_scroll_set(0, wheel);
    const int err = zmk_endpoints_send_mouse_report();
    zmk_hid_mouse_movement_set(0, 0);
    zmk_hid_mouse_scroll_set(0, 0);
    return err;
}

/*
 * Movement is a level: collapsing it across cycles is correct. A button is an edge: a press and its
 * release routinely land in the same cycle (ZMK's combo and tap-dance layers deliver both edges
 * back to back), and reading the mask as a level would then find nothing to send and discard the
 * click. Newly pressed bits stay latched until they have actually gone out; the release follows on
 * the next cycle.
 */
static void output_handler(struct k_work *work) {
    ARG_UNUSED(work);

    k_mutex_lock(&send_lock, K_FOREVER);
    k_mutex_lock(&lock, K_FOREVER);
    if (!enabled) {
        k_mutex_unlock(&lock);
        k_mutex_unlock(&send_lock);
        return;
    }
    const uint32_t gen = generation;
    const int16_t dx = clamp_report(cursor_x);
    const int16_t dy = clamp_report(cursor_y);
    const int16_t wheel = clamp_report(scroll / config.scroll_divisor);
    const uint8_t latched = latch;
    const uint8_t mask = buttons | latched;
    const bool buttons_changed = mask != sent_buttons;
    k_mutex_unlock(&lock);

    if (dx == 0 && dy == 0 && wheel == 0 && !buttons_changed) {
        k_mutex_unlock(&send_lock);
        return;
    }

    const int err = send_report(mask, dx, dy, wheel);

    bool more = false;
    bool retry = false;
    k_mutex_lock(&lock, K_FOREVER);
    if (enabled && generation == gen) {
        if (err == 0) {
            /* Retire only what was delivered; ZMK's own listener zeroes on failure too. */
            cursor_x -= (float)dx;
            cursor_y -= (float)dy;
            scroll -= (float)wheel * config.scroll_divisor;
            sent_buttons = mask;
            latch &= (uint8_t)~latched;
            /* A latched press that was already released still owes the host its release. */
            more = (buttons | latch) != sent_buttons;
        } else {
            retry = (uint32_t)(k_uptime_get_32() - last_input_ms) < RETRY_WINDOW_MS;
        }
    }
    k_mutex_unlock(&lock);
    k_mutex_unlock(&send_lock);

    if (more) {
        submit();
    } else if (retry) {
        k_work_schedule_for_queue(&output_queue, &output_work, K_MSEC(config.retry_ms));
    }
}

void astrolabe_mouse_motion(const float rotation[3], uint32_t now_ms) {
    if (!initialized) {
        return;
    }
    const float rx = rotation[0];
    const float ry = rotation[1];
    const float rz = rotation[2];

    k_mutex_lock(&lock, K_FOREVER);
    if (!enabled) {
        k_mutex_unlock(&lock);
        return;
    }
    if (rx != 0.0f || ry != 0.0f || rz != 0.0f) {
        last_input_ms = now_ms;
        const float twist = fabsf(rz);
        const float tilt = sqrtf(rx * rx + ry * ry);
        const bool scroll_now =
            twist > config.yaw_deadzone && twist > config.yaw_dominance * tilt;
        if (scroll_now) {
            last_scroll_ms = now_ms;
        }
        /* A scroll gesture holds briefly so a twist does not flicker into cursor motion between
         * windows -- unless the window is clearly a tilt, which ends the hold at once. */
        bool scrolling =
            scroll_now || (uint32_t)(now_ms - last_scroll_ms) < config.scroll_hold_ms;
        if (!scroll_now && tilt > config.yaw_deadzone && tilt > config.yaw_dominance * twist) {
            scrolling = false;
        }
        if (scrolling) {
            scroll += rz * config.scroll_gain;
        } else {
            cursor_x += ry * config.cursor_gain;
            cursor_y += rx * config.cursor_gain;
        }
    }
    const bool pending = pending_locked();
    k_mutex_unlock(&lock);

    if (pending) {
        submit();
    }
}

void astrolabe_mouse_buttons(uint8_t press, uint8_t release) {
    if (!initialized) {
        return;
    }
    k_mutex_lock(&lock, K_FOREVER);
    if (!enabled) {
        k_mutex_unlock(&lock);
        return;
    }
    latch |= press & (uint8_t)~buttons;
    buttons = (uint8_t)((buttons | press) & ~release);
    last_input_ms = k_uptime_get_32();
    const bool changed = (buttons | latch) != sent_buttons;
    k_mutex_unlock(&lock);

    if (changed) {
        submit();
    }
}

void astrolabe_mouse_set_enabled(bool enable) {
    k_mutex_lock(&send_lock, K_FOREVER);
    k_mutex_lock(&lock, K_FOREVER);
    enabled = enable;
    ++generation;
    cursor_x = 0.0f;
    cursor_y = 0.0f;
    scroll = 0.0f;
    buttons = 0U;
    latch = 0U;
    const bool owes_release = !enable && (sent_buttons != 0U || applied_buttons != 0U);
    if (!enable) {
        sent_buttons = 0U;
    }
    k_mutex_unlock(&lock);

    if (owes_release) {
        (void)send_report(0U, 0, 0, 0);
    }
    k_mutex_unlock(&send_lock);

    if (!enable && initialized) {
        (void)k_work_cancel_delayable(&output_work);
    }
}

void astrolabe_mouse_init(const struct astrolabe_mouse_config *mouse_config) {
    config = *mouse_config;
    static const struct k_work_queue_config queue_config = {.name = "astrolabe_mouse"};
    k_work_queue_init(&output_queue);
    k_work_queue_start(&output_queue, output_stack, K_THREAD_STACK_SIZEOF(output_stack),
                       OUTPUT_PRIORITY, &queue_config);
    k_work_init_delayable(&output_work, output_handler);
    initialized = true;
}
