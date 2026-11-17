/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * The whole surface a controller needs. A motion source (any driver that can measure rotation)
 * reports radians; keymap behaviors report controls. The core decides where they go: to the host
 * as an ordinary HID mouse, or to a daemon that has claimed the route.
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

/*
 * Report a rotation increment in radians about the housing axes: +X right, +Y away from the user,
 * +Z up (twist), right-handed. Callable from any thread at any rate; the core accumulates and
 * paces delivery itself, so a source never needs to know who is listening.
 */
void astrolabe_report_motion(float rx, float ry, float rz);

/* Report a control by protocol bit (0..7). Repeated reports of the same state are ignored. */
void astrolabe_report_control(uint8_t bit, bool pressed);

/*
 * Optional. `wake` runs on every control press and release, before the control is routed. A
 * source that idles between motions should start sampling: a press is when the ball is about to
 * move, and an optical sensor that has dropped into rest re-locks with a burst of false motion.
 * Holding the sensor in run mode through interaction is what keeps presses from jerking the cursor.
 */
void astrolabe_register_source(void (*wake)(void));
