/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

struct astrolabe_sensor_pose {
    float phi_deg;
    float theta_deg;
    float mount_deg;
    bool flipped;
};

struct astrolabe_fusion {
    float pinv[3][4];
};

bool astrolabe_fusion_init(struct astrolabe_fusion *fusion,
                           const struct astrolabe_sensor_pose poses[2], float frame_tilt_deg);

void astrolabe_fusion_solve(const struct astrolabe_fusion *fusion, const int16_t deltas[4],
                            float omega[3]);
