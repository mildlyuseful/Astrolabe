/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * Output toggle that always reports what happened.
 *
 * Stock `&out OUT_TOG` flips the preferred transport and returns. ZMK then honours that preference
 * only while BOTH transports are ready (get_selected_transport), so toggling with the cable out --
 * or with no BLE host connected -- changes a stored preference, changes nothing observable, and
 * raises no zmk_endpoint_changed. On a device with no screen that is indistinguishable from a dead
 * gesture, which is exactly what it looked like.
 *
 * So: toggle, then compare. If the active transport moved, the endpoint event already drove the
 * LED. If it did not, report the transport still in use with the "not applied" suffix rather than
 * leaving the user to guess.
 *
 * Reporting the CURRENT transport rather than the requested one is deliberate. ZMK exposes no
 * getter for the preference, and current and preferred can legitimately disagree, so the requested
 * transport is not reliably inferable -- while "you are still on BLE" is always true and is the
 * thing worth knowing.
 */

#define DT_DRV_COMPAT astrolabe_behavior_output_toggle

#include <astrolabe/indicator.h>

#include <drivers/behavior.h>
#include <zephyr/device.h>
#include <zephyr/logging/log.h>
#include <zmk/behavior.h>
#include <zmk/endpoints.h>

LOG_MODULE_REGISTER(astrolabe_output, CONFIG_ASTROLABE_LOG_LEVEL);

static int output_pressed(struct zmk_behavior_binding *binding,
                          struct zmk_behavior_binding_event event) {
    ARG_UNUSED(binding);
    ARG_UNUSED(event);

    enum zmk_transport before = zmk_endpoints_selected().transport;

    int ret = zmk_endpoints_toggle_transport();
    if (ret < 0) {
        LOG_WRN("endpoint toggle failed (%d)", ret);
        return ret;
    }

    enum zmk_transport after = zmk_endpoints_selected().transport;
    if (after == before) {
        astrolabe_indicator_show_transport(before, false);
    }

    return 0;
}

static int output_released(struct zmk_behavior_binding *binding,
                           struct zmk_behavior_binding_event event) {
    ARG_UNUSED(binding);
    ARG_UNUSED(event);
    return 0;
}

static const struct behavior_driver_api behavior_api = {
    .binding_pressed = output_pressed,
    .binding_released = output_released,
};

#define ASTROLABE_OUTPUT_DEFINE(index)                                                            \
    BEHAVIOR_DT_INST_DEFINE(index, NULL, NULL, NULL, NULL, POST_KERNEL,                           \
                            CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &behavior_api);

DT_INST_FOREACH_STATUS_OKAY(ASTROLABE_OUTPUT_DEFINE)
