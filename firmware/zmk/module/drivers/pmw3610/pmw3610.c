/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#define DT_DRV_COMPAT astrolabe_dual_pmw3610

#include <astrolabe/fusion.h>
#include <astrolabe/route.h>
#include <astrolabe/sensor.h>

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
    uint16_t shutter;
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
    struct astrolabe_route_config route;
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
    bool sensor_ready[SENSOR_COUNT];
    atomic_t wake_pending;
    atomic_t suspended;
    uint32_t last_activity_ms;
};

static struct pmw_data *active_instance;

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
        .shutter = ((uint16_t)burst[5] << 8) | burst[6],
    };
}

static bool motion_asserted(const struct pmw_config *config, const struct pmw_data *data) {
    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        if (data->sensor_ready[sensor] && gpio_pin_get_dt(&config->motion[sensor]) > 0) {
            return true;
        }
    }
    return false;
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
    const bool pin_active = motion_asserted(config, data);
    const bool active =
        pin_active || (uint32_t)(now - data->last_activity_ms) < config->active_window_ms;
    if (!active) {
        astrolabe_route_flush(now);
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
        const int16_t deltas[4] = {
            samples[0].motion ? samples[0].dx : 0,
            samples[0].motion ? samples[0].dy : 0,
            samples[1].motion ? samples[1].dx : 0,
            samples[1].motion ? samples[1].dy : 0,
        };
        float omega[3];
        astrolabe_fusion_solve(&data->fusion, deltas, omega);
        astrolabe_route_motion(omega[0], omega[1], omega[2], now);
        data->last_activity_ms = now;
    }
    astrolabe_route_flush(now);

    if (!atomic_get(&data->suspended)) {
        k_work_reschedule(&data->poll_work, K_USEC(config->poll_interval_us));
    }
}

static void motion_interrupt(const struct device *port, struct gpio_callback *callback,
                             gpio_port_pins_t pins) {
    ARG_UNUSED(port);
    ARG_UNUSED(pins);
    struct pmw_motion_callback *motion =
        CONTAINER_OF(callback, struct pmw_motion_callback, callback);
    atomic_set(&motion->owner->wake_pending, 1);
    k_work_reschedule(&motion->owner->poll_work, K_NO_WAIT);
}

void astrolabe_pmw_activate(void) {
    if (active_instance == NULL || atomic_get(&active_instance->suspended)) {
        return;
    }
    atomic_set(&active_instance->wake_pending, 1);
    k_work_reschedule(&active_instance->poll_work, K_NO_WAIT);
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
    if (err != 0) {
        return err;
    }
    err = gpio_pin_configure_dt(&config->sdio, GPIO_OUTPUT_ACTIVE);
    if (err != 0) {
        return err;
    }

    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        if (!gpio_is_ready_dt(&config->cs[sensor]) || !gpio_is_ready_dt(&config->motion[sensor])) {
            return -ENODEV;
        }
        err = gpio_pin_configure_dt(&config->cs[sensor], GPIO_OUTPUT_INACTIVE);
        if (err != 0) {
            return err;
        }
        err = gpio_pin_configure_dt(&config->motion[sensor], GPIO_INPUT);
        if (err != 0) {
            return err;
        }
    }

    if (!astrolabe_fusion_init(&data->fusion, config->poses,
                               (float)config->frame_tilt_mdeg / 1000.0f)) {
        return -EINVAL;
    }

    struct astrolabe_route_config route = config->route;
    route.radius_counts =
        ((float)config->ball_diameter_um / 2000.0f) * ((float)config->cpi / 25.4f);
    err = astrolabe_route_init(dev, &route);
    if (err != 0) {
        return err;
    }

    k_mutex_lock(&data->bus_lock, K_FOREVER);
    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        data->sensor_ready[sensor] = sensor_init(config, sensor);
        if (!data->sensor_ready[sensor]) {
            k_msleep(100);
            data->sensor_ready[sensor] = sensor_init(config, sensor);
        }
        if (!data->sensor_ready[sensor]) {
            LOG_WRN("PMW3610 sensor %u did not identify; continuing without it", sensor);
        }
    }
    k_mutex_unlock(&data->bus_lock);

    for (uint8_t sensor = 0; sensor < SENSOR_COUNT; ++sensor) {
        data->motion_callbacks[sensor].owner = data;
        gpio_init_callback(&data->motion_callbacks[sensor].callback, motion_interrupt,
                           BIT(config->motion[sensor].pin));
        err = gpio_add_callback(config->motion[sensor].port,
                                &data->motion_callbacks[sensor].callback);
        if (err != 0) {
            return err;
        }
        err = gpio_pin_interrupt_configure_dt(&config->motion[sensor], GPIO_INT_EDGE_TO_ACTIVE);
        if (err != 0) {
            return err;
        }
    }

    if (!pm_device_wakeup_enable(dev, true)) {
        LOG_ERR("PMW3610 motion GPIOs could not be enabled as a wake source");
        return -ENOTSUP;
    }

    data->last_activity_ms = k_uptime_get_32();
    active_instance = data;
    k_work_reschedule(&data->poll_work, K_NO_WAIT);
    return 0;
}

static int pmw_pm_action(const struct device *dev, enum pm_device_action action) {
    struct pmw_data *data = dev->data;
    switch (action) {
    case PM_DEVICE_ACTION_SUSPEND:
        atomic_set(&data->suspended, 1);
        k_work_cancel_delayable(&data->poll_work);
        return 0;
    case PM_DEVICE_ACTION_RESUME:
        atomic_clear(&data->suspended);
        atomic_set(&data->wake_pending, 1);
        k_work_reschedule(&data->poll_work, K_NO_WAIT);
        return 0;
    default:
        return -ENOTSUP;
    }
}

#define PMW_CONFIG(index)                                                                          \
    {                                                                                              \
        .sclk = GPIO_DT_SPEC_INST_GET(index, sclk_gpios),                                          \
        .sdio = GPIO_DT_SPEC_INST_GET(index, sdio_gpios),                                          \
        .cs = {GPIO_DT_SPEC_INST_GET_BY_IDX(index, cs_gpios, 0),                                   \
               GPIO_DT_SPEC_INST_GET_BY_IDX(index, cs_gpios, 1)},                                  \
        .motion = {GPIO_DT_SPEC_INST_GET_BY_IDX(index, motion_gpios, 0),                           \
                   GPIO_DT_SPEC_INST_GET_BY_IDX(index, motion_gpios, 1)},                          \
        .poses = {{.phi_deg = DT_INST_PROP_BY_IDX(index, sensor_phi_mdeg, 0) / 1000.0f,            \
                   .theta_deg = DT_INST_PROP_BY_IDX(index, sensor_theta_mdeg, 0) / 1000.0f,        \
                   .mount_deg = DT_INST_PROP_BY_IDX(index, sensor_mount_mdeg, 0) / 1000.0f,        \
                   .flipped = DT_INST_PROP_BY_IDX(index, sensor_flipped, 0)},                      \
                  {.phi_deg = DT_INST_PROP_BY_IDX(index, sensor_phi_mdeg, 1) / 1000.0f,            \
                   .theta_deg = DT_INST_PROP_BY_IDX(index, sensor_theta_mdeg, 1) / 1000.0f,        \
                   .mount_deg = DT_INST_PROP_BY_IDX(index, sensor_mount_mdeg, 1) / 1000.0f,        \
                   .flipped = DT_INST_PROP_BY_IDX(index, sensor_flipped, 1)}},                     \
        .frame_tilt_mdeg = DT_INST_PROP(index, frame_tilt_mdeg),                                   \
        .cpi = DT_INST_PROP(index, cpi),                                                           \
        .ball_diameter_um = DT_INST_PROP(index, ball_diameter_um),                                 \
        .poll_interval_us = DT_INST_PROP(index, poll_interval_us),                                 \
        .active_window_ms = DT_INST_PROP(index, active_window_ms),                                 \
        .route = {.cursor_gain = DT_INST_PROP(index, cursor_gain_milli) / 1000.0f,                 \
                  .scroll_gain = DT_INST_PROP(index, scroll_gain_milli) / 1000.0f,                 \
                  .scroll_divisor = DT_INST_PROP(index, scroll_divisor),                           \
                  .yaw_deadzone = DT_INST_PROP(index, yaw_deadzone_milli) / 1000.0f,               \
                  .yaw_dominance = DT_INST_PROP(index, yaw_dominance_milli) / 1000.0f,             \
                  .scroll_hold_ms = DT_INST_PROP(index, scroll_hold_ms),                           \
                  .send_interval_ms = DT_INST_PROP(index, send_interval_ms)},                      \
    }

#define PMW_DEFINE(index)                                                                          \
    static const struct pmw_config config_##index = PMW_CONFIG(index);                             \
    static struct pmw_data data_##index;                                                           \
    PM_DEVICE_DT_INST_DEFINE(index, pmw_pm_action);                                                \
    DEVICE_DT_INST_DEFINE(index, pmw_init, PM_DEVICE_DT_INST_GET(index), &data_##index,            \
                          &config_##index, POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

DT_INST_FOREACH_STATUS_OKAY(PMW_DEFINE)
