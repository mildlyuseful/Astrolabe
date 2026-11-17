/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Status LED. Three of the four standalone gestures change radio or endpoint state on a device
 * with no screen, so without this "nothing happened" and "it worked" look the same.
 *
 * Event-driven: dark at rest, blinking only on a change, so it costs no battery while nobody is
 * looking. Patterns name the state the device ended up in, not the action taken -- ZMK raises the
 * same profile event for a switch, a bond clear, and a connection, carrying only an index:
 *
 *   Endpoint  long, then 1 short = USB, 2 short = BLE
 *   Profile   N short = profile N (one-based)
 *   Suffix    trailing long = what you selected is not usable (no bond / toggle not applied)
 */

#include "core.h"

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <zmk/ble.h>
#include <zmk/event_manager.h>
#include <zmk/events/ble_active_profile_changed.h>
#include <zmk/events/endpoint_changed.h>

LOG_MODULE_REGISTER(astrolabe_indicator, CONFIG_ASTROLABE_LOG_LEVEL);

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(astrolabe_status_led), gpios);

#define SHORT_ON 90
#define SHORT_OFF 170
#define LONG_ON 520
#define LONG_OFF 220
#define LEAD_GAP 300
/* Longest pattern: five short pulses plus a trailing long, two entries each. */
#define STEPS_MAX 16

struct pattern {
    uint16_t step[STEPS_MAX]; /* even indices on-times, odd indices off-times */
    uint8_t len;
};

static struct pattern active;
static uint8_t position;
static struct k_spinlock lock;

static void blink_step(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(blink_work, blink_step);

static void add(struct pattern *p, uint16_t on_ms, uint16_t off_ms) {
    if (p->len + 2 <= STEPS_MAX) {
        p->step[p->len++] = on_ms;
        p->step[p->len++] = off_ms;
    }
}

/* Latest wins: a repeated gesture restarts the pattern, so the LED describes now. */
static void publish(const struct pattern *p) {
    K_SPINLOCK(&lock) {
        active = *p;
        position = 0;
    }
    k_work_reschedule(&blink_work, K_NO_WAIT);
}

static void blink_step(struct k_work *work) {
    ARG_UNUSED(work);
    uint16_t ms = 0;
    bool on = false;
    bool done = true;
    K_SPINLOCK(&lock) {
        if (position < active.len) {
            on = (position % 2) == 0;
            ms = active.step[position++];
            done = false;
        }
    }
    gpio_pin_set_dt(&led, done ? 0 : on);
    if (!done) {
        k_work_reschedule(&blink_work, K_MSEC(ms));
    }
}

void astrolabe_indicator_show_transport(enum zmk_transport transport, bool applied) {
    struct pattern p = {0};
    add(&p, LONG_ON, LEAD_GAP);
    for (uint8_t i = 0; i < (transport == ZMK_TRANSPORT_USB ? 1 : 2); i++) {
        add(&p, SHORT_ON, SHORT_OFF);
    }
    if (!applied) {
        add(&p, LONG_ON, LONG_OFF);
    }
    publish(&p);
}

static void show_profile(uint8_t index) {
    struct pattern p = {0};
    for (uint8_t i = 0; i <= index; i++) {
        add(&p, SHORT_ON, SHORT_OFF);
    }
    if (zmk_ble_profile_is_open(index)) {
        add(&p, LONG_ON, LONG_OFF);
    }
    publish(&p);
}

static enum zmk_transport last_transport;
static bool last_transport_valid;

static int indicator_listener(const zmk_event_t *eh) {
    const struct zmk_endpoint_changed *endpoint = as_zmk_endpoint_changed(eh);
    if (endpoint != NULL) {
        /* A BLE profile switch also raises an endpoint event (the BLE endpoint embeds the profile
         * index). Report endpoints only when the transport itself changed; show_profile covers
         * the rest. */
        const bool changed =
            !last_transport_valid || endpoint->endpoint.transport != last_transport;
        last_transport = endpoint->endpoint.transport;
        last_transport_valid = true;
        if (changed) {
            astrolabe_indicator_show_transport(endpoint->endpoint.transport, true);
        }
        return ZMK_EV_EVENT_BUBBLE;
    }
    const struct zmk_ble_active_profile_changed *profile = as_zmk_ble_active_profile_changed(eh);
    if (profile != NULL) {
        show_profile(profile->index);
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(astrolabe_indicator, indicator_listener);
ZMK_SUBSCRIPTION(astrolabe_indicator, zmk_endpoint_changed);
ZMK_SUBSCRIPTION(astrolabe_indicator, zmk_ble_active_profile_changed);

static int indicator_init(void) {
    if (!gpio_is_ready_dt(&led) || gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE) < 0) {
        LOG_ERR("status LED unavailable");
        return -ENODEV;
    }
    return 0;
}

SYS_INIT(indicator_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
