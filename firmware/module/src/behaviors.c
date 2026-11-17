/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Keymap behaviors.
 *
 *   &astro BIT   reports a control. One parameter, so it also fits inside a hold-tap, which hands
 *                each of its bindings exactly one. The standalone click a bit produces comes from
 *                the controller's control-buttons property, not from the keymap.
 *   &astro_out   toggles USB/BLE output and always reports the outcome on the status LED. Stock
 *                &out OUT_TOG is silent when the toggle cannot take effect: ZMK honours the
 *                preferred transport only while both are ready, so toggling with the cable out
 *                changes a stored preference, nothing observable, and raises no event.
 */

#include <astrolabe/astrolabe.h>

#include "core.h"

#include <drivers/behavior.h>
#include <zephyr/device.h>
#include <zephyr/logging/log.h>
#include <zmk/behavior.h>
#include <zmk/endpoints.h>

LOG_MODULE_REGISTER(astrolabe_behaviors, CONFIG_ASTROLABE_LOG_LEVEL);

#define DT_DRV_COMPAT astrolabe_behavior_control

static int control_pressed(struct zmk_behavior_binding *binding,
                           struct zmk_behavior_binding_event event) {
    ARG_UNUSED(event);
    astrolabe_report_control((uint8_t)binding->param1, true);
    return ZMK_BEHAVIOR_OPAQUE;
}

static int control_released(struct zmk_behavior_binding *binding,
                            struct zmk_behavior_binding_event event) {
    ARG_UNUSED(event);
    astrolabe_report_control((uint8_t)binding->param1, false);
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api control_api = {
    .binding_pressed = control_pressed,
    .binding_released = control_released,
};

#define CONTROL_DEFINE(index)                                                                     \
    BEHAVIOR_DT_INST_DEFINE(index, NULL, NULL, NULL, NULL, POST_KERNEL,                           \
                            CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &control_api);

DT_INST_FOREACH_STATUS_OKAY(CONTROL_DEFINE)

#undef DT_DRV_COMPAT
#define DT_DRV_COMPAT astrolabe_behavior_output_toggle

static int output_pressed(struct zmk_behavior_binding *binding,
                          struct zmk_behavior_binding_event event) {
    ARG_UNUSED(binding);
    ARG_UNUSED(event);
    const enum zmk_transport before = zmk_endpoints_selected().transport;
    const int err = zmk_endpoints_toggle_transport();
    if (err < 0) {
        LOG_WRN("output toggle failed (%d)", err);
        return err;
    }
    /* A transport that did change raises zmk_endpoint_changed, which drives the LED already. ZMK
     * has no getter for the preference, so report the transport still in use: always true, and
     * the thing worth knowing. */
    if (zmk_endpoints_selected().transport == before) {
        astrolabe_indicator_show_transport(before, false);
    }
    return ZMK_BEHAVIOR_OPAQUE;
}

static int output_released(struct zmk_behavior_binding *binding,
                           struct zmk_behavior_binding_event event) {
    ARG_UNUSED(binding);
    ARG_UNUSED(event);
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api output_api = {
    .binding_pressed = output_pressed,
    .binding_released = output_released,
};

#define OUTPUT_DEFINE(index)                                                                      \
    BEHAVIOR_DT_INST_DEFINE(index, NULL, NULL, NULL, NULL, POST_KERNEL,                           \
                            CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &output_api);

DT_INST_FOREACH_STATUS_OKAY(OUTPUT_DEFINE)
