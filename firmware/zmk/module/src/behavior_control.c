/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#define DT_DRV_COMPAT astrolabe_behavior_control

#include <astrolabe/route.h>
#include <astrolabe/sensor.h>

#include <drivers/behavior.h>
#include <zephyr/device.h>
#include <zephyr/logging/log.h>
#include <zmk/behavior.h>

LOG_MODULE_REGISTER(astrolabe_control, CONFIG_ASTROLABE_LOG_LEVEL);

static int binding_pressed(struct zmk_behavior_binding *binding,
                           struct zmk_behavior_binding_event event) {
    ARG_UNUSED(event);
    astrolabe_pmw_activate();
    astrolabe_route_control((uint8_t)binding->param1, (uint8_t)binding->param2, true);
    return 0;
}

static int binding_released(struct zmk_behavior_binding *binding,
                            struct zmk_behavior_binding_event event) {
    ARG_UNUSED(event);
    astrolabe_pmw_activate();
    astrolabe_route_control((uint8_t)binding->param1, (uint8_t)binding->param2, false);
    return 0;
}

static const struct behavior_driver_api behavior_api = {
    .binding_pressed = binding_pressed,
    .binding_released = binding_released,
};

#define ASTROLABE_CONTROL_DEFINE(index)                                                           \
    BEHAVIOR_DT_INST_DEFINE(index, NULL, NULL, NULL, NULL, POST_KERNEL,                           \
                            CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &behavior_api);

DT_INST_FOREACH_STATUS_OKAY(ASTROLABE_CONTROL_DEFINE)
