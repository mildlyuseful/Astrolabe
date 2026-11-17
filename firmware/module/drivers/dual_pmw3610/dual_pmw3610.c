/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Motion source for the reference trackball: two PMW3610 sensors on a shared three-wire bus,
 * looking at one ball from different poses. Least-squares fusion turns their four deltas into
 * three-axis ball rotation, which is reported to the core in radians.
 *
 * The sensors keep their own automatic run/rest policy. A MOTION edge (or a control press, via
 * the core's wake hook) opens an active window in which the MCU polls both sensors at a fixed
 * interval; when the window closes, polling stops until the next edge.
 */

#define DT_DRV_COMPAT astrolabe_dual_pmw3610

#include <astrolabe/astrolabe.h>

#include "fusion.h"

#include <errno.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/pm/device.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/util.h>

LOG_MODULE_REGISTER(astrolabe_pmw3610, CONFIG_ASTROLABE_LOG_LEVEL);

#define SENSOR_COUNT 2U
#define BURST_BYTES 7U

#define REG_PRODUCT_ID 0x00U
#define REG_MOTION 0x02U
#define REG_DELTA_XY_H 0x05U
#define REG_PERFORMANCE 0x11U
#define REG_BURST_READ 0x12U
#define REG_RUN_DOWNSHIFT 0x1BU
#define REG_REST1_RATE 0x1CU
#define REG_REST1_DOWNSHIFT 0x1DU
#define REG_OBSERVATION1 0x2DU
#define REG_POWER_UP_RESET 0x3AU
#define REG_NOT_PRODUCT_ID 0x3FU
#define REG_SPI_CLK_ON_REQ 0x41U
#define REG_SPI_PAGE 0x7FU
#define REG_RES_STEP 0x05U

#define PMW3610_PRODUCT_ID 0x3EU
#define PMW3610_NOT_PRODUCT_ID 0xC1U
#define SPI_CLK_ON 0xBAU
#define SPI_CLK_OFF 0xB5U
#define SPI_PAGE0 0x00U
#define SPI_PAGE1 0xFFU
#define PERFORMANCE_AUTOMATIC_REST 0x0DU
#define RUN_DOWNSHIFT_INIT 0x04U
#define REST1_RATE_INIT 0x04U
#define REST1_DOWNSHIFT_INIT 0x0FU

/* Bus timing, microseconds (PixArt datasheet minimums). */
#define T_SCLK_HALF_US 2U
#define T_NCS_SCLK_US 1U
#define T_SRAD_US 35U
#define T_SRR_US 20U
#define T_SWW_US 120U
#define T_SCLK_NCS_WRITE_US 10U
#define T_BUS_EXIT_US 4U
#define T_CLK_ON_US 300U

struct pmw_sample {
    bool motion;
    int16_t dx;
    int16_t dy;
    uint8_t squal;
};

struct pmw_config {
    struct gpio_dt_spec sclk;
    struct gpio_dt_spec sdio;
    struct gpio_dt_spec cs[SENSOR_COUNT];
    struct gpio_dt_spec motion[SENSOR_COUNT];
    struct astrolabe_sensor_pose poses[SENSOR_COUNT];
    int32_t frame_tilt_mdeg;
    uint32_t cpi;
    uint32_t ball_diameter_um;
    uint32_t poll_interval_us;
    uint32_t active_window_ms;
};

struct pmw_data;

struct pmw_motion_callback {
    struct gpio_callback callback;
    struct pmw_data *owner;
};

struct pmw_data {
    const struct device *dev;
    struct k_mutex bus_lock;
    struct k_work_delayable poll_work;
    struct pmw_motion_callback motion_callbacks[SENSOR_COUNT];
    struct astrolabe_fusion fusion;
    float counts_per_radian;
    bool sensor_ready[SENSOR_COUNT];
    atomic_t wake_pending;
    atomic_t suspended;
    atomic_t chain_running;
    uint32_t last_activity_ms;
};

static struct pmw_data *active_instance;

/* --- Three-wire bus, bit-banged ---------------------------------------------------------------- */

static void sclk_set(const struct pmw_config *config, int value) {
    gpio_pin_set_dt(&config->sclk, value);
}

static void sdio_drive(const struct pmw_config *config, int value) {
    gpio_pin_configure_dt(&config->sdio, GPIO_OUTPUT);
    gpio_pin_set_dt(&config->sdio, value);
}

static void sdio_release(const struct pmw_config *config) {
    gpio_pin_configure_dt(&config->sdio, GPIO_INPUT);
}

static void write_byte(const struct pmw_config *config, uint8_t value) {
    sdio_drive(config, 0);
    for (uint8_t bit = 0; bit < 8U; ++bit) {
        sclk_set(config, 0);
        gpio_pin_set_dt(&config->sdio, (value & 0x80U) != 0U);
        k_busy_wait(T_SCLK_HALF_US);
        sclk_set(config, 1);
        k_busy_wait(T_SCLK_HALF_US);
        value <<= 1;
    }
}

static uint8_t read_byte(const struct pmw_config *config) {
    uint8_t value = 0U;
    for (uint8_t bit = 0; bit < 8U; ++bit) {
        sclk_set(config, 0);
        k_busy_wait(T_SCLK_HALF_US);
        sclk_set(config, 1);
        value = (uint8_t)((value << 1) | (gpio_pin_get_dt(&config->sdio) > 0));
        k_busy_wait(T_SCLK_HALF_US);
    }
    return value;
}

static void cs_assert(const struct pmw_config *config, uint8_t sensor) {
    gpio_pin_set_dt(&config->cs[sensor], 1);
    k_busy_wait(T_NCS_SCLK_US);
}

static void cs_deassert(const struct pmw_config *config, uint8_t sensor) {
    gpio_pin_set_dt(&config->cs[sensor], 0);
    k_busy_wait(T_BUS_EXIT_US);
}

static uint8_t read_register(const struct pmw_config *config, uint8_t sensor, uint8_t address) {
    sclk_set(config, 1);
    cs_assert(config, sensor);
    write_byte(config, address & 0x7FU);
    sdio_release(config);
    k_busy_wait(T_SRAD_US);
    const uint8_t value = read_byte(config);
    cs_deassert(config, sensor);
    sdio_drive(config, 1);
    k_busy_wait(T_SRR_US);
    return value;
}

static void write_register(const struct pmw_config *config, uint8_t sensor, uint8_t address,
                           uint8_t value) {
    sclk_set(config, 1);
    cs_assert(config, sensor);
    write_byte(config, address | 0x80U);
    write_byte(config, value);
    k_busy_wait(T_SCLK_NCS_WRITE_US);
    cs_deassert(config, sensor);
    sdio_drive(config, 1);
    k_busy_wait(T_SWW_US);
}

/* Motion Burst rather than discrete register reads: a burst is one coherent frame, where discrete
 * reads interrupted by a radio IRQ can tear dx from dy. */
static void read_burst(const struct pmw_config *config, uint8_t sensor,
                       uint8_t output[BURST_BYTES]) {
    sclk_set(config, 1);
    cs_assert(config, sensor);
    write_byte(config, REG_BURST_READ);
    sdio_release(config);
    k_busy_wait(T_SRAD_US);
    for (uint8_t i = 0; i < BURST_BYTES; ++i) {
        output[i] = read_byte(config);
    }
    cs_deassert(config, sensor);
    sdio_drive(config, 1);
    k_busy_wait(T_SRR_US);
}

/* --- Sensor setup ------------------------------------------------------------------------------ */

static void spi_clock(const struct pmw_config *config, uint8_t sensor, bool enabled) {
    write_register(config, sensor, REG_SPI_CLK_ON_REQ, enabled ? SPI_CLK_ON : SPI_CLK_OFF);
    if (enabled) {
        k_busy_wait(T_CLK_ON_US);
    }
}

static void set_cpi(const struct pmw_config *config, uint8_t sensor) {
    uint32_t cpi = CLAMP(config->cpi, 200U, 3200U);
    cpi = (cpi / 200U) * 200U;
    spi_clock(config, sensor, true);
    write_register(config, sensor, REG_SPI_PAGE, SPI_PAGE1);
    uint8_t resolution = read_register(config, sensor, REG_RES_STEP);
    resolution = (uint8_t)((resolution & ~0x1FU) | (cpi / 200U));
    write_register(config, sensor, REG_RES_STEP, resolution);
    write_register(config, sensor, REG_SPI_PAGE, SPI_PAGE0);
    spi_clock(config, sensor, false);
}

static bool sensor_init(const struct pmw_config *config, uint8_t sensor) {
    write_register(config, sensor, REG_POWER_UP_RESET, 0x5AU);
    k_msleep(50);

    if (read_register(config, sensor, REG_PRODUCT_ID) != PMW3610_PRODUCT_ID ||
        read_register(config, sensor, REG_NOT_PRODUCT_ID) != PMW3610_NOT_PRODUCT_ID) {
        return false;
    }

    spi_clock(config, sensor, true);
    write_register(config, sensor, REG_OBSERVATION1, 0x00U);
    k_msleep(10);
    (void)read_register(config, sensor, REG_OBSERVATION1);
    for (uint8_t reg = REG_MOTION; reg <= REG_DELTA_XY_H; ++reg) {
        (void)read_register(config, sensor, reg);
    }
    write_register(config, sensor, REG_PERFORMANCE, PERFORMANCE_AUTOMATIC_REST);
    write_register(config, sensor, REG_RUN_DOWNSHIFT, RUN_DOWNSHIFT_INIT);
    write_register(config, sensor, REG_REST1_RATE, REST1_RATE_INIT);
    write_register(config, sensor, REG_REST1_DOWNSHIFT, REST1_DOWNSHIFT_INIT);
    spi_clock(config, sensor, false);
    set_cpi(config, sensor);
    return true;
}

static int16_t sign_extend_12(uint16_t value) {
    if ((value & 0x0800U) != 0U) {
        value |= 0xF000U;
    }
    return (int16_t)value;
}

static struct pmw_sample sample_sensor(const struct pmw_config *config, uint8_t sensor) {
    uint8_t burst[BURST_BYTES];
    read_burst(config, sensor, burst);
    return (struct pmw_sample){
        .motion = (burst[0] & 0x80U) != 0U,
        .dx = sign_extend_12((uint16_t)(((burst[3] & 0xF0U) << 4) | burst[1])),
        .dy = sign_extend_12((uint16_t)(((burst[3] & 0x0FU) << 8) | burst[2])),
        .squal = burst[4],
    };
}

/* --- Polling ----------------------------------------------------------------------------------- */

static bool motion_asserted(const struct pmw_config *config, const struct pmw_data *data) {
    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        if (data->sensor_ready[sensor] && gpio_pin_get_dt(&config->motion[sensor]) > 0) {
            return true;
        }
    }
    return false;
}

/* Start a poll chain unless one is already running. Never reschedule a running chain: it re-arms
 * itself every poll interval, and pulling it forward on every edge makes it free-run (each burst
 * read clears MOTION, the sensor re-asserts within a millisecond), saturating the cooperative
 * system workqueue and starving everything preemptible behind it. */
static void start_chain(struct pmw_data *data) {
    atomic_set(&data->wake_pending, 1);
    if (atomic_cas(&data->chain_running, 0, 1)) {
        k_work_reschedule(&data->poll_work, K_NO_WAIT);
    }
}

static void poll_work_handler(struct k_work *work) {
    struct pmw_data *data = CONTAINER_OF(work, struct pmw_data, poll_work.work);
    const struct pmw_config *config = data->dev->config;
    if (atomic_get(&data->suspended)) {
        return;
    }

    const uint32_t now = k_uptime_get_32();
    if (atomic_set(&data->wake_pending, 0) != 0) {
        data->last_activity_ms = now;
    }
    const bool active = motion_asserted(config, data) ||
                        (uint32_t)(now - data->last_activity_ms) < config->active_window_ms;
    if (!active) {
        atomic_clear(&data->chain_running);
        /* An edge that arrived while the chain was still marked running was dropped, and an
         * edge-triggered interrupt will not fire again for a level that is already asserted. */
        if (atomic_get(&data->wake_pending) != 0 && atomic_cas(&data->chain_running, 0, 1)) {
            k_work_reschedule(&data->poll_work, K_NO_WAIT);
        }
        return;
    }

    struct pmw_sample samples[SENSOR_COUNT] = {0};
    k_mutex_lock(&data->bus_lock, K_FOREVER);
    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        if (data->sensor_ready[sensor]) {
            samples[sensor] = sample_sensor(config, sensor);
        }
    }
    k_mutex_unlock(&data->bus_lock);

    if (samples[0].motion || samples[1].motion) {
        /* A sensor without MOTION contributes zero rows rather than being dropped: the solve
         * needs all four, and the single-sensor error cancels across a delivery window. */
        const int16_t deltas[4] = {
            samples[0].motion ? samples[0].dx : 0,
            samples[0].motion ? samples[0].dy : 0,
            samples[1].motion ? samples[1].dx : 0,
            samples[1].motion ? samples[1].dy : 0,
        };
#if IS_ENABLED(CONFIG_ASTROLABE_CALIBRATION_LOG)
        /* Raw deltas for tools/calibrate_sensor_mounts.py. */
        LOG_INF("CALIB,%d,%d,%d,%d,%u,%u", deltas[0], deltas[1], deltas[2], deltas[3],
                samples[0].squal, samples[1].squal);
#endif
        float omega[3];
        astrolabe_fusion_solve(&data->fusion, deltas, omega);
        /* Fusion solves in sensor counts about the housing frame; the protocol carries the
         * opposite sense in radians, so the conversion and the sign live here, once. */
        const float scale = -1.0f / data->counts_per_radian;
        astrolabe_report_motion(omega[0] * scale, omega[1] * scale, omega[2] * scale);
        data->last_activity_ms = now;
    }

    if (!atomic_get(&data->suspended)) {
        k_work_reschedule(&data->poll_work, K_USEC(config->poll_interval_us));
    }
}

static void motion_interrupt(const struct device *port, struct gpio_callback *callback,
                             gpio_port_pins_t pins) {
    ARG_UNUSED(port);
    ARG_UNUSED(pins);
    start_chain(CONTAINER_OF(callback, struct pmw_motion_callback, callback)->owner);
}

/* The core's wake hook: a control press holds the sensors' run mode through the interaction, so
 * the press does not land on a rest-to-run wake transient (a burst of false lockstep motion while
 * the sensor's exposure servo re-locks). */
static void wake(void) {
    struct pmw_data *data = active_instance;
    if (data != NULL && !atomic_get(&data->suspended)) {
        start_chain(data);
    }
}

static int pmw_init(const struct device *dev) {
    const struct pmw_config *config = dev->config;
    struct pmw_data *data = dev->data;
    data->dev = dev;
    k_mutex_init(&data->bus_lock);
    k_work_init_delayable(&data->poll_work, poll_work_handler);
    atomic_clear(&data->wake_pending);
    atomic_clear(&data->suspended);

    if (!gpio_is_ready_dt(&config->sclk) || !gpio_is_ready_dt(&config->sdio)) {
        return -ENODEV;
    }
    int err = gpio_pin_configure_dt(&config->sclk, GPIO_OUTPUT_ACTIVE);
    if (err == 0) {
        err = gpio_pin_configure_dt(&config->sdio, GPIO_OUTPUT_ACTIVE);
    }
    for (uint8_t sensor = 0; err == 0 && sensor < SENSOR_COUNT; ++sensor) {
        if (!gpio_is_ready_dt(&config->cs[sensor]) || !gpio_is_ready_dt(&config->motion[sensor])) {
            return -ENODEV;
        }
        err = gpio_pin_configure_dt(&config->cs[sensor], GPIO_OUTPUT_INACTIVE);
        if (err == 0) {
            err = gpio_pin_configure_dt(&config->motion[sensor], GPIO_INPUT);
        }
    }
    if (err != 0) {
        return err;
    }

    if (!astrolabe_fusion_init(&data->fusion, config->poses,
                               (float)config->frame_tilt_mdeg / 1000.0f)) {
        return -EINVAL;
    }
    /* Radius in sensor counts: one radian of ball rotation moves the surface one radius. */
    data->counts_per_radian =
        ((float)config->ball_diameter_um / 2000.0f) * ((float)config->cpi / 25.4f);

    k_mutex_lock(&data->bus_lock, K_FOREVER);
    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        data->sensor_ready[sensor] = sensor_init(config, sensor);
        if (!data->sensor_ready[sensor]) {
            k_msleep(100);
            data->sensor_ready[sensor] = sensor_init(config, sensor);
        }
    }
    k_mutex_unlock(&data->bus_lock);
    /* A missing sensor does not fail loudly: it zeroes two of four rows, which attenuates and
     * cross-couples the solve. Say so, so it is not mistaken for a tuning problem. */
    LOG_INF("PMW3610 sensors ready: 0=%d 1=%d", data->sensor_ready[0], data->sensor_ready[1]);

    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        data->motion_callbacks[sensor].owner = data;
        gpio_init_callback(&data->motion_callbacks[sensor].callback, motion_interrupt,
                           BIT(config->motion[sensor].pin));
        err = gpio_add_callback(config->motion[sensor].port,
                                &data->motion_callbacks[sensor].callback);
        if (err == 0) {
            err = gpio_pin_interrupt_configure_dt(&config->motion[sensor],
                                                  GPIO_INT_EDGE_TO_ACTIVE);
        }
        if (err != 0) {
            return err;
        }
    }

    if (!pm_device_wakeup_enable(dev, true)) {
        LOG_ERR("PMW3610 motion pins could not be enabled as a wake source");
        return -ENOTSUP;
    }

    data->last_activity_ms = k_uptime_get_32();
    active_instance = data;
    astrolabe_register_source(wake);
    atomic_set(&data->chain_running, 1);
    k_work_reschedule(&data->poll_work, K_NO_WAIT);
    return 0;
}

static int pmw_pm_action(const struct device *dev, enum pm_device_action action) {
    struct pmw_data *data = dev->data;
    switch (action) {
    case PM_DEVICE_ACTION_SUSPEND:
        atomic_set(&data->suspended, 1);
        k_work_cancel_delayable(&data->poll_work);
        atomic_clear(&data->chain_running);
        return 0;
    case PM_DEVICE_ACTION_RESUME:
        atomic_clear(&data->suspended);
        atomic_set(&data->wake_pending, 1);
        atomic_set(&data->chain_running, 1);
        k_work_reschedule(&data->poll_work, K_NO_WAIT);
        return 0;
    default:
        return -ENOTSUP;
    }
}

#define POSE(index, sensor)                                                                       \
    {                                                                                             \
        .phi_deg = DT_INST_PROP_BY_IDX(index, sensor_phi_mdeg, sensor) / 1000.0f,                 \
        .theta_deg = DT_INST_PROP_BY_IDX(index, sensor_theta_mdeg, sensor) / 1000.0f,             \
        .mount_deg = DT_INST_PROP_BY_IDX(index, sensor_mount_mdeg, sensor) / 1000.0f,             \
        .flipped = DT_INST_PROP_BY_IDX(index, sensor_flipped, sensor),                            \
    }

#define PMW_DEFINE(index)                                                                         \
    static const struct pmw_config config_##index = {                                            \
        .sclk = GPIO_DT_SPEC_INST_GET(index, sclk_gpios),                                         \
        .sdio = GPIO_DT_SPEC_INST_GET(index, sdio_gpios),                                         \
        .cs = {GPIO_DT_SPEC_INST_GET_BY_IDX(index, cs_gpios, 0),                                  \
               GPIO_DT_SPEC_INST_GET_BY_IDX(index, cs_gpios, 1)},                                 \
        .motion = {GPIO_DT_SPEC_INST_GET_BY_IDX(index, motion_gpios, 0),                          \
                   GPIO_DT_SPEC_INST_GET_BY_IDX(index, motion_gpios, 1)},                         \
        .poses = {POSE(index, 0), POSE(index, 1)},                                                \
        .frame_tilt_mdeg = DT_INST_PROP(index, frame_tilt_mdeg),                                  \
        .cpi = DT_INST_PROP(index, cpi),                                                          \
        .ball_diameter_um = DT_INST_PROP(index, ball_diameter_um),                                \
        .poll_interval_us = DT_INST_PROP(index, poll_interval_us),                                \
        .active_window_ms = DT_INST_PROP(index, active_window_ms),                                \
    };                                                                                            \
    static struct pmw_data data_##index;                                                          \
    PM_DEVICE_DT_INST_DEFINE(index, pmw_pm_action);                                               \
    DEVICE_DT_INST_DEFINE(index, pmw_init, PM_DEVICE_DT_INST_GET(index), &data_##index,           \
                          &config_##index, POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

DT_INST_FOREACH_STATUS_OKAY(PMW_DEFINE)
