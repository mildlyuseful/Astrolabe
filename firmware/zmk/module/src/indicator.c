// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

/*
 * Status LED. Three of the four standalone gestures change radio or endpoint state on a device with
 * no screen, so without this "nothing happened" and "it worked" are the same observation.
 *
 * Event-driven, not a state display: the LED is dark at rest and only speaks when something
 * changes. This is a battery device whose discharge curve is not measured yet, and a repeating
 * pattern would burn runtime during the vast majority of the time nobody is looking at it.
 *
 * The vocabulary keys off resulting STATE, not off the action taken, because it has to: ZMK raises
 * zmk_ble_active_profile_changed for a profile switch, a bond clear, and a connection alike, and
 * the event carries only an index. Patterns named after actions would be ambiguous; patterns naming
 * the state the device ended up in are not.
 *
 *   Endpoint  long, then 1 short = USB, 2 short = BLE
 *   Profile   N short = profile N
 *   Suffix    one long = the thing you selected is not usable (no bond / request not applied)
 *
 * A leading long means "endpoint", a leading short means "profile", a trailing long means "not
 * usable". That is the whole grammar, and it is what keeps them readable apart at a glance.
 *
 * Endpoint patterns are gated on the TRANSPORT changing, not on zmk_endpoint_changed firing. The
 * BLE endpoint instance embeds profile_index and ZMK's own endpoint listener subscribes to
 * zmk_ble_active_profile_changed, so switching profile while on BLE raises an endpoint event too.
 * Reporting that verbatim made a profile switch blink the endpoint pattern whenever USB was
 * unplugged, while looking correct whenever it was not.
 *
 * A bond clear on an already-unbonded profile emits nothing, because ZMK raises no event for it --
 * clear_profile_bond() returns early when the peer is already BT_ADDR_LE_ANY. Clearing twice
 * therefore looks like a dead device. It is not worth firmware to paper over, but it is worth
 * knowing before diagnosing one.
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>
#include <string.h>

#include <astrolabe/indicator.h>

#include <zmk/ble.h>
#include <zmk/endpoints_types.h>
#include <zmk/event_manager.h>
#include <zmk/events/ble_active_profile_changed.h>
#include <zmk/events/endpoint_changed.h>

LOG_MODULE_REGISTER(astrolabe_indicator, CONFIG_ASTROLABE_LOG_LEVEL);

/* Aliased in the shield rather than referenced by the board's `blue_led` label, which names the
 * colour on a genuine nice!nano; the same pin drives the red LED here. */
static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(astrolabe_status_led), gpios);

#define SHORT_ON   90
#define SHORT_OFF  170
#define LONG_ON    520
#define LONG_OFF   220
#define LEAD_GAP   300

/* Longest pattern is five short pulses plus a trailing long: 6 steps, 2 entries each. */
#define STEPS_MAX 16

struct pattern {
    uint16_t step[STEPS_MAX];
    uint8_t len;
};

static struct pattern active;
static uint8_t position;
static struct k_spinlock lock;

static void blink_step(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(blink_work, blink_step);

static void add(struct pattern *p, uint16_t on_ms, uint16_t off_ms) {
    if (p->len + 2 > STEPS_MAX) {
        return;
    }
    p->step[p->len++] = on_ms;
    p->step[p->len++] = off_ms;
}

/* Latest wins. Repeating a gesture restarts the pattern rather than queueing behind the previous
 * one, so the LED always describes where the device is now, not where it was two presses ago. */
static void publish(const struct pattern *p) {
    K_SPINLOCK(&lock) {
        active = *p;
        position = 0;
    }
    k_work_reschedule(&blink_work, K_NO_WAIT);
}

static void blink_step(struct k_work *work) {
    uint16_t ms = 0;
    bool on = false;
    bool done = true;

    ARG_UNUSED(work);

    K_SPINLOCK(&lock) {
        if (position < active.len) {
            /* Even indices are on-times, odd are off-times. */
            on = (position % 2) == 0;
            ms = active.step[position++];
            done = false;
        }
    }

    if (done) {
        gpio_pin_set_dt(&led, 0);
        return;
    }

    gpio_pin_set_dt(&led, on ? 1 : 0);
    k_work_reschedule(&blink_work, K_MSEC(ms));
}

void astrolabe_indicator_show_transport(enum zmk_transport transport, bool applied) {
    struct pattern p = {0};
    uint8_t count = transport == ZMK_TRANSPORT_USB ? 1 : 2;

    add(&p, LONG_ON, LEAD_GAP);
    for (uint8_t i = 0; i < count; i++) {
        add(&p, SHORT_ON, SHORT_OFF);
    }
    if (!applied) {
        add(&p, LONG_ON, LONG_OFF);
    }

    publish(&p);
}

static void show_profile(uint8_t index) {
    struct pattern p = {0};

    /* One-based so profile 0 is a single blink rather than silence. */
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
    const struct zmk_endpoint_changed *ep = as_zmk_endpoint_changed(eh);
    if (ep != NULL) {
        /* Ignore an instance change that kept the same transport: that is a BLE profile switch
         * arriving through the endpoint event, and show_profile already reports it. */
        bool changed = !last_transport_valid || ep->endpoint.transport != last_transport;

        last_transport = ep->endpoint.transport;
        last_transport_valid = true;

        if (changed) {
            astrolabe_indicator_show_transport(ep->endpoint.transport, true);
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
    if (!gpio_is_ready_dt(&led)) {
        LOG_ERR("status LED port not ready");
        return -ENODEV;
    }

    if (gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE) < 0) {
        LOG_ERR("cannot drive status LED on pin %u", led.pin);
        return -EIO;
    }

    return 0;
}

SYS_INIT(indicator_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
