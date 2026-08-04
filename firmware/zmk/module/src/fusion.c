/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#include <astrolabe/fusion.h>

#include <math.h>
#include <stddef.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static void cross3(const float a[3], const float b[3], float out[3]) {
    out[0] = a[1] * b[2] - a[2] * b[1];
    out[1] = a[2] * b[0] - a[0] * b[2];
    out[2] = a[0] * b[1] - a[1] * b[0];
}

static float norm3(const float value[3]) {
    return sqrtf(value[0] * value[0] + value[1] * value[1] + value[2] * value[2]);
}

static void local_frame(const float normal[3], float azimuth[3], float up[3]) {
    static const float z_axis[3] = {0.0f, 0.0f, 1.0f};
    cross3(z_axis, normal, azimuth);
    const float azimuth_norm = norm3(azimuth);
    if (azimuth_norm < 1.0e-6f) {
        azimuth[0] = 1.0f;
        azimuth[1] = 0.0f;
        azimuth[2] = 0.0f;
    } else {
        for (size_t i = 0; i < 3; ++i) {
            azimuth[i] /= azimuth_norm;
        }
    }

    cross3(normal, azimuth, up);
    const float up_norm = norm3(up);
    for (size_t i = 0; i < 3; ++i) {
        up[i] /= up_norm;
    }
}

static void sensor_axes(const float normal[3], const struct astrolabe_sensor_pose *pose,
                        float dx_dir[3], float dy_dir[3]) {
    float azimuth[3];
    float up[3];
    local_frame(normal, azimuth, up);

    const float angle = pose->mount_deg * (float)M_PI / 180.0f;
    const float cosine = cosf(angle);
    const float sine = sinf(angle);
    const float flip = pose->flipped ? -1.0f : 1.0f;
    for (size_t i = 0; i < 3; ++i) {
        const float base_x = -azimuth[i];
        dx_dir[i] = cosine * base_x + sine * up[i];
        dy_dir[i] = (-sine * base_x + cosine * up[i]) * flip;
    }
}

static void tilt_to_housing(float vector[3], float tilt_deg) {
    const float angle = tilt_deg * (float)M_PI / 180.0f;
    const float cosine = cosf(angle);
    const float sine = sinf(angle);
    const float x = vector[0];
    const float z = vector[2];
    vector[0] = cosine * x + sine * z;
    vector[2] = -sine * x + cosine * z;
}

static bool invert3x3(const float matrix[3][3], float inverse[3][3]) {
    const float determinant =
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1]) -
        matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0]) +
        matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0]);
    if (fabsf(determinant) < 1.0e-9f) {
        return false;
    }

    const float scale = 1.0f / determinant;
    inverse[0][0] = (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1]) * scale;
    inverse[0][1] = -(matrix[0][1] * matrix[2][2] - matrix[0][2] * matrix[2][1]) * scale;
    inverse[0][2] = (matrix[0][1] * matrix[1][2] - matrix[0][2] * matrix[1][1]) * scale;
    inverse[1][0] = -(matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0]) * scale;
    inverse[1][1] = (matrix[0][0] * matrix[2][2] - matrix[0][2] * matrix[2][0]) * scale;
    inverse[1][2] = -(matrix[0][0] * matrix[1][2] - matrix[0][2] * matrix[1][0]) * scale;
    inverse[2][0] = (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0]) * scale;
    inverse[2][1] = -(matrix[0][0] * matrix[2][1] - matrix[0][1] * matrix[2][0]) * scale;
    inverse[2][2] = (matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]) * scale;
    return true;
}

bool astrolabe_fusion_init(struct astrolabe_fusion *fusion,
                           const struct astrolabe_sensor_pose poses[2], float frame_tilt_deg) {
    if (fusion == NULL || poses == NULL) {
        return false;
    }

    float rows[4][3];
    for (size_t sensor = 0; sensor < 2; ++sensor) {
        const float phi = poses[sensor].phi_deg * (float)M_PI / 180.0f;
        const float theta = poses[sensor].theta_deg * (float)M_PI / 180.0f;
        const float normal[3] = {sinf(theta) * cosf(phi), sinf(theta) * sinf(phi), cosf(theta)};
        float dx_dir[3];
        float dy_dir[3];
        sensor_axes(normal, &poses[sensor], dx_dir, dy_dir);
        cross3(normal, dx_dir, rows[sensor * 2]);
        cross3(normal, dy_dir, rows[sensor * 2 + 1]);
        tilt_to_housing(rows[sensor * 2], frame_tilt_deg);
        tilt_to_housing(rows[sensor * 2 + 1], frame_tilt_deg);
    }

    float ata[3][3] = {{0.0f}};
    for (size_t i = 0; i < 3; ++i) {
        for (size_t j = 0; j < 3; ++j) {
            for (size_t row = 0; row < 4; ++row) {
                ata[i][j] += rows[row][i] * rows[row][j];
            }
        }
    }

    float inverse[3][3];
    if (!invert3x3(ata, inverse)) {
        return false;
    }

    for (size_t i = 0; i < 3; ++i) {
        for (size_t column = 0; column < 4; ++column) {
            fusion->pinv[i][column] = 0.0f;
            for (size_t j = 0; j < 3; ++j) {
                fusion->pinv[i][column] += inverse[i][j] * rows[column][j];
            }
        }
    }
    return true;
}

void astrolabe_fusion_solve(const struct astrolabe_fusion *fusion, const int16_t deltas[4],
                            float omega[3]) {
    for (size_t axis = 0; axis < 3; ++axis) {
        omega[axis] = 0.0f;
        for (size_t sample = 0; sample < 4; ++sample) {
            omega[axis] += fusion->pinv[axis][sample] * (float)deltas[sample];
        }
    }
}
