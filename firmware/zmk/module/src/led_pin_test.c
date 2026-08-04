// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

/*
 * Which physical LED is the board's one GPIO LED?
 *
 * nice_nano_v2 declares exactly one, `blue_led` at P0.15, named for the colour it is on a real
 * nice!nano. The SuperMini is a pin-compatible clone whose user LED is red and whose blue LED is a
 * charge indicator the charging circuit owns, so the label may be inherited rather than accurate.
 *
 * The pattern is three fast pulses then a long dark gap, which is what makes this answerable: a
 * charge indicator sits steady and the bootloader's flash only happens at reset, so neither can be
 * mistaken for it. Whichever LED pulses in threes is P0.15.
 *
 * Diagnostic only. CONFIG_ASTROLABE_LED_PIN_TEST defaults to n and nothing selects it.
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(astrolabe_led_test, CONFIG_ASTROLABE_LOG_LEVEL);

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_NODELABEL(blue_led), gpios);

#define PULSE_MS 120
#define GAP_MS   1200

static void blink_step(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(blink_work, blink_step);

static void blink_step(struct k_work *work) {
    /* Phases 0-5 are the three on/off pulses; phase 6 is the dark gap that makes them countable. */
    static uint8_t phase;

    ARG_UNUSED(work);

    if (phase < 6) {
        gpio_pin_set_dt(&led, (phase % 2) == 0);
        k_work_reschedule(&blink_work, K_MSEC(PULSE_MS));
    } else {
        gpio_pin_set_dt(&led, 0);
        k_work_reschedule(&blink_work, K_MSEC(GAP_MS));
    }

    phase = (phase + 1) % 7;
}

static int led_pin_test_init(void) {
    if (!gpio_is_ready_dt(&led)) {
        LOG_ERR("LED pin test: GPIO port not ready");
        return -ENODEV;
    }

    if (gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE) < 0) {
        LOG_ERR("LED pin test: cannot drive pin %u", led.pin);
        return -EIO;
    }

    /* Warning level so it survives the default log filter without raising it. */
    LOG_WRN("LED pin test active on pin %u: three pulses then a gap", led.pin);
    k_work_reschedule(&blink_work, K_MSEC(500));
    return 0;
}

SYS_INIT(led_pin_test_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
