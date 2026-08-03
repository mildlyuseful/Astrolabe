/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#include <astrolabe/protocol.h>
#include <astrolabe/route.h>

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
#include <zephyr/usb/usb_ch9.h>
#include <zephyr/usb/class/usb_hid.h>
#include <zephyr/usb/usb_device.h>

#include <zmk/event_manager.h>
#include <zmk/events/usb_conn_state_changed.h>
#include <zmk/usb.h>

LOG_MODULE_REGISTER(astrolabe_usb, CONFIG_ASTROLABE_LOG_LEVEL);

#define REPORT_ROTATION 1U
#define REPORT_SNAPSHOT 2U
#define REPORT_COMMAND 3U
#define REPORT_CAPABILITY 4U
#define REPORT_ACK 5U

#define COMMAND_ATTACH 1U
#define COMMAND_DETACH 2U
#define COMMAND_KEEPALIVE 3U

#define ACK_OK 0U
#define ACK_FORCED_STANDALONE 1U
#define ACK_INVALID 2U

#define OWNER_STANDALONE 0U
#define OWNER_BLE 1U
#define OWNER_USB 2U

#define COMMAND_PAYLOAD_BYTES 4U
#define CAPABILITY_PAYLOAD_BYTES 8U
#define CAPABILITY_REPORT_BYTES (1U + CAPABILITY_PAYLOAD_BYTES)
#define ACK_PAYLOAD_BYTES 6U
#define MAX_REPORT_BYTES (1U + ASTROLABE_ROTATION_PACKET_BYTES)
#define KEEPALIVE_TIMEOUT_MS 1500U
#define HID_REPORT_TYPE_OUTPUT 2U
#define HID_REPORT_TYPE_FEATURE 3U

/* Vendor page 0xFF00, usage 1. Every count excludes the report-ID byte. */
static const uint8_t report_descriptor[] = {
    0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08,
    0x85, REPORT_ROTATION, 0x09, 0x01, 0x95, ASTROLABE_ROTATION_PACKET_BYTES, 0x81, 0x02,
    0x85, REPORT_SNAPSHOT, 0x09, 0x02, 0x95, ASTROLABE_INPUT_PACKET_BYTES, 0x81, 0x02,
    0x85, REPORT_COMMAND, 0x09, 0x03, 0x95, COMMAND_PAYLOAD_BYTES, 0x91, 0x02,
    0x85, REPORT_CAPABILITY, 0x09, 0x04, 0x95, CAPABILITY_PAYLOAD_BYTES, 0xB1, 0x02,
    0x85, REPORT_ACK, 0x09, 0x05, 0x95, ACK_PAYLOAD_BYTES, 0x81, 0x02,
    0xC0,
};

struct tx_report {
    uint8_t length;
    astrolabe_route_lease_t lease;
    uint8_t data[MAX_REPORT_BYTES];
};

struct command_report {
    uint8_t opcode;
    uint16_t request_id;
};

K_MSGQ_DEFINE(ack_queue, sizeof(struct tx_report), 8, 4);
K_MSGQ_DEFINE(data_queue, sizeof(struct tx_report), 12, 4);
K_MSGQ_DEFINE(command_queue, sizeof(struct command_report), 8, 4);

static const struct device *hid_device;
static struct k_work tx_work;
static struct k_work command_work;
static struct k_work_delayable ownership_work;
static atomic_t tx_busy;
static struct k_mutex session_lock;
static astrolabe_route_lease_t usb_lease;
static uint32_t keepalive_deadline;
static uint8_t capability[CAPABILITY_REPORT_BYTES];

static uint8_t owner_code(void) {
    switch (astrolabe_route_current()) {
    case ASTROLABE_ROUTE_BLE_DAEMON:
        return OWNER_BLE;
    case ASTROLABE_ROUTE_USB_DAEMON:
        return OWNER_USB;
    default:
        return OWNER_STANDALONE;
    }
}

static int queue_report(struct k_msgq *queue, uint8_t report_id, const uint8_t *payload,
                        size_t length, astrolabe_route_lease_t lease) {
    if (length + 1U > MAX_REPORT_BYTES || hid_device == NULL) {
        return -EINVAL;
    }
    struct tx_report report = {
        .length = (uint8_t)(length + 1U),
        .lease = lease,
        .data = {report_id},
    };
    memcpy(&report.data[1], payload, length);
    const int err = k_msgq_put(queue, &report, K_NO_WAIT);
    if (err == 0) {
        k_work_submit(&tx_work);
    }
    return err;
}

static astrolabe_route_lease_t current_usb_lease(void) {
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_route_lease_t lease = usb_lease;
    k_mutex_unlock(&session_lock);
    return lease;
}

/* Deliberately does not clear tx_busy. That flag means "an IN transfer is outstanding", which is
 * a property of the endpoint, not of the session: int_in_ready clears it on completion and
 * resubmits tx_work, so a later ACK drains on its own. Clearing it here would let the next report
 * be written while the previous transfer is still in flight, and usb_write's -EAGAIN is treated as
 * fatal by tx_handler. Only a bus-level event stops completions arriving, and usb_event_listener
 * already clears the flag there. */
static bool clear_local_session(astrolabe_route_lease_t lease) {
    bool cleared = false;
    k_mutex_lock(&session_lock, K_FOREVER);
    if (usb_lease == lease && lease != ASTROLABE_ROUTE_LEASE_NONE) {
        usb_lease = ASTROLABE_ROUTE_LEASE_NONE;
        keepalive_deadline = 0U;
        k_msgq_purge(&data_queue);
        k_msgq_purge(&ack_queue);
        cleared = true;
    }
    k_mutex_unlock(&session_lock);
    return cleared;
}

void astrolabe_usb_route_revoked(astrolabe_route_lease_t lease) {
    if (clear_local_session(lease)) {
        (void)k_work_cancel_delayable(&ownership_work);
    }
}

static void release_usb_owner(astrolabe_route_lease_t lease) {
    astrolabe_route_release(ASTROLABE_ROUTE_USB_DAEMON, lease);
    astrolabe_usb_route_revoked(lease);
}

static void tx_handler(struct k_work *work) {
    ARG_UNUSED(work);
    if (!atomic_cas(&tx_busy, 0, 1)) {
        return;
    }

    struct tx_report report;
    if (k_msgq_get(&ack_queue, &report, K_NO_WAIT) != 0 &&
        k_msgq_get(&data_queue, &report, K_NO_WAIT) != 0) {
        atomic_clear(&tx_busy);
        return;
    }

    int err;
    if (report.lease != ASTROLABE_ROUTE_LEASE_NONE) {
        k_mutex_lock(&session_lock, K_FOREVER);
        if (usb_lease != report.lease ||
            !astrolabe_route_lease_is_current(ASTROLABE_ROUTE_USB_DAEMON, report.lease)) {
            k_mutex_unlock(&session_lock);
            atomic_clear(&tx_busy);
            k_work_submit(&tx_work);
            return;
        }
        err = hid_int_ep_write(hid_device, report.data, report.length, NULL);
        k_mutex_unlock(&session_lock);
    } else {
        err = hid_int_ep_write(hid_device, report.data, report.length, NULL);
    }
    if (err != 0) {
        atomic_clear(&tx_busy);
        const astrolabe_route_lease_t lease = current_usb_lease();
        if (lease != ASTROLABE_ROUTE_LEASE_NONE) {
            release_usb_owner(lease);
        }
        k_msgq_purge(&ack_queue);
    }
}

static void input_ready(const struct device *dev) {
    ARG_UNUSED(dev);
    atomic_clear(&tx_busy);
    k_work_submit(&tx_work);
}

static int send_ack(uint8_t opcode, uint16_t request_id, uint8_t result) {
    uint8_t payload[ACK_PAYLOAD_BYTES] = {
        ASTROLABE_PROTOCOL_VERSION, opcode, 0U, 0U, result, owner_code(),
    };
    sys_put_le16(request_id, &payload[2]);
    return queue_report(&ack_queue, REPORT_ACK, payload, sizeof(payload),
                        ASTROLABE_ROUTE_LEASE_NONE);
}

static bool install_usb_owner(astrolabe_route_lease_t lease) {
    k_mutex_lock(&session_lock, K_FOREVER);
    const bool current = astrolabe_route_lease_is_current(ASTROLABE_ROUTE_USB_DAEMON, lease);
    if (current) {
        usb_lease = lease;
    }
    k_mutex_unlock(&session_lock);
    return current;
}

static void arm_keepalive(astrolabe_route_lease_t lease) {
    k_mutex_lock(&session_lock, K_FOREVER);
    if (usb_lease != lease) {
        k_mutex_unlock(&session_lock);
        return;
    }
    keepalive_deadline = k_uptime_get_32() + KEEPALIVE_TIMEOUT_MS;
    k_mutex_unlock(&session_lock);
    k_work_reschedule(&ownership_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

static void command_handler(struct k_work *work) {
    ARG_UNUSED(work);
    struct command_report command;
    while (k_msgq_get(&command_queue, &command, K_NO_WAIT) == 0) {
        uint8_t result = ACK_OK;
        switch (command.opcode) {
        case COMMAND_ATTACH: {
            astrolabe_route_lease_t lease;
            const int err = astrolabe_route_claim(ASTROLABE_ROUTE_USB_DAEMON, &lease);
            if (err == -EPERM) {
                result = ACK_FORCED_STANDALONE;
            } else if (err != 0) {
                result = ACK_INVALID;
            } else if (!install_usb_owner(lease)) {
                astrolabe_route_release(ASTROLABE_ROUTE_USB_DAEMON, lease);
                result = ACK_INVALID;
            } else {
                arm_keepalive(lease);
            }
            (void)send_ack(command.opcode, command.request_id, result);
            if (result == ACK_OK) {
                (void)astrolabe_route_publish_snapshot(ASTROLABE_ROUTE_USB_DAEMON, lease);
            }
            break;
        }
        case COMMAND_DETACH: {
            const astrolabe_route_lease_t lease = current_usb_lease();
            if (lease != ASTROLABE_ROUTE_LEASE_NONE) {
                release_usb_owner(lease);
            }
            (void)send_ack(command.opcode, command.request_id, ACK_OK);
            break;
        }
        case COMMAND_KEEPALIVE: {
            const astrolabe_route_lease_t lease = current_usb_lease();
            if (!astrolabe_route_lease_is_current(ASTROLABE_ROUTE_USB_DAEMON, lease)) {
                result = ACK_INVALID;
            } else {
                arm_keepalive(lease);
            }
        }
            (void)send_ack(command.opcode, command.request_id, result);
            break;
        default:
            (void)send_ack(command.opcode, command.request_id, ACK_INVALID);
            break;
        }
    }
}

static void ownership_handler(struct k_work *work) {
    ARG_UNUSED(work);
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_route_lease_t lease = usb_lease;
    const uint32_t deadline = keepalive_deadline;
    k_mutex_unlock(&session_lock);
    if (lease == ASTROLABE_ROUTE_LEASE_NONE ||
        !astrolabe_route_lease_is_current(ASTROLABE_ROUTE_USB_DAEMON, lease)) {
        astrolabe_usb_route_revoked(lease);
        return;
    }
    const uint32_t now = k_uptime_get_32();
    if ((int32_t)(now - deadline) >= 0) {
        release_usb_owner(lease);
        return;
    }
    k_work_reschedule(&ownership_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

static void queue_command(const uint8_t payload[COMMAND_PAYLOAD_BYTES]) {
    if (payload[0] != ASTROLABE_PROTOCOL_VERSION) {
        const uint16_t request_id = sys_get_le16(&payload[2]);
        (void)send_ack(payload[1], request_id, ACK_INVALID);
        return;
    }
    const struct command_report command = {
        .opcode = payload[1],
        .request_id = sys_get_le16(&payload[2]),
    };
    if (k_msgq_put(&command_queue, &command, K_NO_WAIT) == 0) {
        k_work_submit(&command_work);
    } else {
        (void)send_ack(command.opcode, command.request_id, ACK_INVALID);
    }
}

static void output_ready(const struct device *dev) {
    uint8_t report[1U + COMMAND_PAYLOAD_BYTES];
    uint32_t length = 0U;
    if (hid_int_ep_read(dev, report, sizeof(report), &length) == 0 && length == sizeof(report) &&
        report[0] == REPORT_COMMAND) {
        queue_command(&report[1]);
    }
}

static int get_report(const struct device *dev, struct usb_setup_packet *setup, int32_t *length,
                      uint8_t **data) {
    ARG_UNUSED(dev);
    if ((setup->wValue & 0xFFU) != REPORT_CAPABILITY ||
        (setup->wValue >> 8) != HID_REPORT_TYPE_FEATURE) {
        return -ENOTSUP;
    }
    capability[0] = REPORT_CAPABILITY;
    capability[1] = ASTROLABE_PROTOCOL_VERSION;
    capability[2] = ASTROLABE_FIRMWARE_PROTOCOL_REV;
    capability[3] = BIT(0) | BIT(1) | BIT(2);
    capability[4] = BIT(0) | BIT(1) | (astrolabe_route_forced_standalone() ? BIT(2) : 0U);
    sys_put_le16(KEEPALIVE_TIMEOUT_MS, &capability[5]);
    capability[7] = 1U;
    capability[8] = 0U;
    *data = capability;
    *length = MIN(*length, (int32_t)sizeof(capability));
    return 0;
}

static int set_report(const struct device *dev, struct usb_setup_packet *setup, int32_t *length,
                      uint8_t **data) {
    ARG_UNUSED(dev);
    if ((setup->wValue & 0xFFU) != REPORT_COMMAND ||
        (setup->wValue >> 8) != HID_REPORT_TYPE_OUTPUT) {
        return -ENOTSUP;
    }
    const uint8_t *payload = *data;
    if (*length == (int32_t)(1U + COMMAND_PAYLOAD_BYTES) && payload[0] == REPORT_COMMAND) {
        ++payload;
    } else if (*length != COMMAND_PAYLOAD_BYTES) {
        return -ENOTSUP;
    }
    queue_command(payload);
    return 0;
}

static const struct hid_ops hid_operations = {
    .get_report = get_report,
    .set_report = set_report,
    .int_in_ready = input_ready,
    .int_out_ready = output_ready,
};

int astrolabe_usb_rotation(const uint8_t payload[12]) {
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_route_lease_t lease = usb_lease;
    if (!astrolabe_route_lease_is_current(ASTROLABE_ROUTE_USB_DAEMON, lease)) {
        k_mutex_unlock(&session_lock);
        return -ENOTCONN;
    }
    const int err =
        queue_report(&data_queue, REPORT_ROTATION, payload, ASTROLABE_ROTATION_PACKET_BYTES, lease);
    k_mutex_unlock(&session_lock);
    return err;
}

int astrolabe_usb_snapshot(const uint8_t payload[6]) {
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_route_lease_t lease = usb_lease;
    if (!astrolabe_route_lease_is_current(ASTROLABE_ROUTE_USB_DAEMON, lease)) {
        k_mutex_unlock(&session_lock);
        return -ENOTCONN;
    }
    const int err =
        queue_report(&data_queue, REPORT_SNAPSHOT, payload, ASTROLABE_INPUT_PACKET_BYTES, lease);
    k_mutex_unlock(&session_lock);
    return err;
}

static int usb_event_listener(const zmk_event_t *event) {
    const struct zmk_usb_conn_state_changed *changed = as_zmk_usb_conn_state_changed(event);
    if (changed == NULL) {
        return -ENOTSUP;
    }
    if (changed->conn_state == ZMK_USB_CONN_NONE || zmk_usb_get_status() == USB_DC_SUSPEND) {
        const astrolabe_route_lease_t lease = current_usb_lease();
        if (lease != ASTROLABE_ROUTE_LEASE_NONE) {
            release_usb_owner(lease);
        }
        k_msgq_purge(&ack_queue);
        k_msgq_purge(&command_queue);
        atomic_clear(&tx_busy);
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(astrolabe_usb_listener, usb_event_listener);
ZMK_SUBSCRIPTION(astrolabe_usb_listener, zmk_usb_conn_state_changed);

static int usb_init(void) {
    /* Two couplings to ZMK that a version bump can break silently:
     * - "_1" is the second HID instance. ZMK registers "_0" for its own keyboard/mouse endpoint,
     *   so CONFIG_USB_HID_DEVICE_COUNT must stay >= 2 and ZMK must keep claiming instance 0.
     * - The init priority below must stay under ZMK's, because usb_hid_register_device has to run
     *   before ZMK calls usb_enable(). Today ZMK is at CONFIG_ZMK_USB_INIT_PRIORITY=94 and
     *   CONFIG_ZMK_USB_HID_INIT_PRIORITY=95; 89 leaves headroom. If USB stops enumerating the
     *   vendor interface after a ZMK bump, check those two values first. */
    hid_device = device_get_binding(CONFIG_USB_HID_DEVICE_NAME "_1");
    if (hid_device == NULL) {
        return -ENODEV;
    }
    k_work_init(&tx_work, tx_handler);
    k_work_init(&command_work, command_handler);
    k_work_init_delayable(&ownership_work, ownership_handler);
    k_mutex_init(&session_lock);
    atomic_clear(&tx_busy);
    usb_lease = ASTROLABE_ROUTE_LEASE_NONE;
    usb_hid_register_device(hid_device, report_descriptor, sizeof(report_descriptor),
                            &hid_operations);
    return usb_hid_init(hid_device);
}

SYS_INIT(usb_init, APPLICATION, 89);
