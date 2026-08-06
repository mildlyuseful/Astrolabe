/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <zmk/endpoints_types.h>
#include <stdbool.h>

/*
 * Report the selected transport on the status LED.
 *
 * `applied` false means the request could not take effect and `transport` is what the device is
 * still using. ZMK honours the preferred transport only while both are ready, so toggling with a
 * cable unplugged saves a preference that changes nothing now -- and raises no event, which is why
 * this cannot be driven from zmk_endpoint_changed alone.
 */
void astrolabe_indicator_show_transport(enum zmk_transport transport, bool applied);
