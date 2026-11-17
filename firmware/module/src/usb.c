/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * USB daemon transport: a second, vendor-defined HID interface beside ZMK's own.
 *
 * Input reports carry rotation, snapshots, and command ACKs; an output report carries commands
 * (attach, detach, keepalive, bootloader); a feature report describes the interface. An attach
 * claims the route and keepalives hold it, exactly as on BLE, with USB preferred over BLE.
 */

#include "core.h"

#include <errno.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/util.h>
/* usb_ch9.h first: usb_hid.h uses struct usb_setup_packet without declaring it. */
#include <zephyr/usb/usb_ch9.h>
#include <zephyr/usb/class/usb_hid.h>
#include <zephyr/usb/usb_device.h>

#include <dt-bindings/zmk/reset.h>
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
#define COMMAND_BOOTLOADER 4U

#define ACK_OK 0U
#define ACK_FORCED_STANDALONE 1U
#define ACK_INVALID 2U

#define OWNER_STANDALONE 0U
#define OWNER_BLE 1U
#define OWNER_USB 2U

/* Capability flags. */
#define CAN_ATTACH BIT(0)
#define CAN_KEEPALIVE BIT(1)
#define IS_FORCED_STANDALONE BIT(2)
#define CAN_BOOTLOADER BIT(3)

#define COMMAND_PAYLOAD_BYTES 4U
#define CAPABILITY_PAYLOAD_BYTES 8U
#define ACK_PAYLOAD_BYTES 6U
#define MAX_REPORT_BYTES (1U + ASTROLABE_ROTATION_BYTES)
#define KEEPALIVE_TIMEOUT_MS 1500U
#define HID_REPORT_TYPE_OUTPUT 2U
#define HID_REPORT_TYPE_FEATURE 3U

/* Vendor page 0xFF00, usage 1. Report counts exclude the report-ID byte. */
static const uint8_t report_descriptor[] = {
    0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08,
    0x85, REPORT_ROTATION, 0x09, 0x01, 0x95, ASTROLABE_ROTATION_BYTES, 0x81, 0x02,
    0x85, REPORT_SNAPSHOT, 0x09, 0x02, 0x95, ASTROLABE_SNAPSHOT_BYTES, 0x81, 0x02,
    0x85, REPORT_COMMAND, 0x09, 0x03, 0x95, COMMAND_PAYLOAD_BYTES, 0x91, 0x02,
    0x85, REPORT_CAPABILITY, 0x09, 0x04, 0x95, CAPABILITY_PAYLOAD_BYTES, 0xB1, 0x02,
    0x85, REPORT_ACK, 0x09, 0x05, 0x95, ACK_PAYLOAD_BYTES, 0x81, 0x02,
    0xC0,
};

struct tx_report {
    uint8_t length;
    astrolabe_lease_t lease; /* NONE for ACKs, which answer whoever asked */
    uint8_t data[MAX_REPORT_BYTES];
};

struct command {
    uint8_t opcode;
    uint16_t request_id;
};

/* ACKs drain ahead of data so a command is never answered behind a backlog of rotation. */
K_MSGQ_DEFINE(ack_queue, sizeof(struct tx_report), 8, 4);
K_MSGQ_DEFINE(data_queue, sizeof(struct tx_report), 12, 4);
K_MSGQ_DEFINE(command_queue, sizeof(struct command), 8, 4);

static const struct device *hid_device;
static struct k_work tx_work;
static struct k_work command_work;
static struct k_work_delayable keepalive_work;
static struct k_work_delayable bootloader_work;
/* An IN transfer is outstanding. A property of the endpoint, not of the session: only completion
 * (input_ready) or a bus-level reset clears it. */
static atomic_t tx_busy;
static K_MUTEX_DEFINE(session_lock);
static astrolabe_lease_t session_lease;
static uint32_t keepalive_deadline;

static uint8_t owner_code(void) {
    switch (astrolabe_route_owner()) {
    case ASTROLABE_ROUTE_BLE:
        return OWNER_BLE;
    case ASTROLABE_ROUTE_USB:
        return OWNER_USB;
    default:
        return OWNER_STANDALONE;
    }
}

static int queue_report(struct k_msgq *queue, uint8_t report_id, const uint8_t *payload,
                        size_t length, astrolabe_lease_t lease) {
    if (hid_device == NULL || length + 1U > MAX_REPORT_BYTES) {
        return -EINVAL;
    }
    struct tx_report report = {.length = (uint8_t)(length + 1U), .lease = lease};
    report.data[0] = report_id;
    memcpy(&report.data[1], payload, length);
    const int err = k_msgq_put(queue, &report, K_NO_WAIT);
    if (err == 0) {
        k_work_submit(&tx_work);
    }
    return err;
}

static astrolabe_lease_t current_lease(void) {
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_lease_t lease = session_lease;
    k_mutex_unlock(&session_lock);
    return lease;
}

static void revoked(astrolabe_lease_t lease) {
    bool cleared = false;
    k_mutex_lock(&session_lock, K_FOREVER);
    if (lease != ASTROLABE_LEASE_NONE && session_lease == lease) {
        session_lease = ASTROLABE_LEASE_NONE;
        keepalive_deadline = 0U;
        k_msgq_purge(&data_queue);
        k_msgq_purge(&ack_queue);
        cleared = true;
    }
    k_mutex_unlock(&session_lock);
    if (cleared) {
        (void)k_work_cancel_delayable(&keepalive_work);
    }
}

static void release_session(void) {
    const astrolabe_lease_t lease = current_lease();
    if (lease != ASTROLABE_LEASE_NONE) {
        astrolabe_route_release(ASTROLABE_ROUTE_USB, lease);
        revoked(lease);
    }
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
    k_mutex_lock(&session_lock, K_FOREVER);
    if (report.lease != ASTROLABE_LEASE_NONE &&
        (session_lease != report.lease ||
         !astrolabe_route_is_current(ASTROLABE_ROUTE_USB, report.lease))) {
        /* Built for a session that has since ended: drop it and move on to the next. */
        k_mutex_unlock(&session_lock);
        atomic_clear(&tx_busy);
        k_work_submit(&tx_work);
        return;
    }
    err = hid_int_ep_write(hid_device, report.data, report.length, NULL);
    k_mutex_unlock(&session_lock);

    if (err != 0) {
        atomic_clear(&tx_busy);
        release_session();
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
    return queue_report(&ack_queue, REPORT_ACK, payload, sizeof(payload), ASTROLABE_LEASE_NONE);
}

static void arm_keepalive(astrolabe_lease_t lease) {
    k_mutex_lock(&session_lock, K_FOREVER);
    if (session_lease != lease) {
        k_mutex_unlock(&session_lock);
        return;
    }
    keepalive_deadline = k_uptime_get_32() + KEEPALIVE_TIMEOUT_MS;
    k_mutex_unlock(&session_lock);
    k_work_reschedule(&keepalive_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

static uint8_t attach(astrolabe_lease_t *lease) {
    const int err = astrolabe_route_claim(ASTROLABE_ROUTE_USB, lease);
    if (err == -EPERM) {
        return ACK_FORCED_STANDALONE;
    }
    if (err != 0) {
        return ACK_INVALID;
    }
    k_mutex_lock(&session_lock, K_FOREVER);
    const bool current = astrolabe_route_is_current(ASTROLABE_ROUTE_USB, *lease);
    if (current) {
        session_lease = *lease;
    }
    k_mutex_unlock(&session_lock);
    if (!current) {
        astrolabe_route_release(ASTROLABE_ROUTE_USB, *lease);
        return ACK_INVALID;
    }
    arm_keepalive(*lease);
    return ACK_OK;
}

static void reboot_to_bootloader(struct k_work *work) {
    ARG_UNUSED(work);
    /* RST_UF2 is the magic the UF2 bootloader checks for in GPREGRET; this is what ZMK's own
     * &bootloader behavior does, minus the three-second hold. */
    sys_reboot(RST_UF2);
}

static void command_handler(struct k_work *work) {
    ARG_UNUSED(work);
    struct command command;
    while (k_msgq_get(&command_queue, &command, K_NO_WAIT) == 0) {
        switch (command.opcode) {
        case COMMAND_ATTACH: {
            astrolabe_lease_t lease;
            const uint8_t result = attach(&lease);
            (void)send_ack(command.opcode, command.request_id, result);
            if (result == ACK_OK) {
                (void)astrolabe_route_publish_snapshot(ASTROLABE_ROUTE_USB, lease);
            }
            break;
        }
        case COMMAND_DETACH:
            release_session();
            (void)send_ack(command.opcode, command.request_id, ACK_OK);
            break;
        case COMMAND_KEEPALIVE: {
            const astrolabe_lease_t lease = current_lease();
            const bool current = astrolabe_route_is_current(ASTROLABE_ROUTE_USB, lease);
            if (current) {
                arm_keepalive(lease);
            }
            (void)send_ack(command.opcode, command.request_id, current ? ACK_OK : ACK_INVALID);
            break;
        }
        case COMMAND_BOOTLOADER:
            (void)send_ack(command.opcode, command.request_id, ACK_OK);
            /* Give the ACK time to leave before the USB stack goes away. */
            k_work_reschedule(&bootloader_work, K_MSEC(100));
            break;
        default:
            (void)send_ack(command.opcode, command.request_id, ACK_INVALID);
            break;
        }
    }
}

static void keepalive_check(struct k_work *work) {
    ARG_UNUSED(work);
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_lease_t lease = session_lease;
    const uint32_t deadline = keepalive_deadline;
    k_mutex_unlock(&session_lock);
    if (lease == ASTROLABE_LEASE_NONE ||
        !astrolabe_route_is_current(ASTROLABE_ROUTE_USB, lease)) {
        revoked(lease);
        return;
    }
    if ((int32_t)(k_uptime_get_32() - deadline) >= 0) {
        LOG_WRN("USB daemon keepalive expired, releasing the route");
        release_session();
        return;
    }
    k_work_reschedule(&keepalive_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

static void queue_command(const uint8_t payload[COMMAND_PAYLOAD_BYTES]) {
    const struct command command = {
        .opcode = payload[1],
        .request_id = sys_get_le16(&payload[2]),
    };
    if (payload[0] != ASTROLABE_PROTOCOL_VERSION ||
        k_msgq_put(&command_queue, &command, K_NO_WAIT) != 0) {
        (void)send_ack(command.opcode, command.request_id, ACK_INVALID);
        return;
    }
    k_work_submit(&command_work);
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
    static uint8_t capability[1U + CAPABILITY_PAYLOAD_BYTES];
    if ((setup->wValue & 0xFFU) != REPORT_CAPABILITY ||
        (setup->wValue >> 8) != HID_REPORT_TYPE_FEATURE) {
        return -ENOTSUP;
    }
    capability[0] = REPORT_CAPABILITY;
    capability[1] = ASTROLABE_PROTOCOL_VERSION;
    capability[2] = ASTROLABE_FIRMWARE_PROTOCOL_REV;
    capability[3] = BIT(0) | BIT(1) | BIT(2); /* rotation, snapshot, ACK reports */
    capability[4] = CAN_ATTACH | CAN_KEEPALIVE | CAN_BOOTLOADER |
                    (astrolabe_route_forced_standalone() ? IS_FORCED_STANDALONE : 0U);
    sys_put_le16(KEEPALIVE_TIMEOUT_MS, &capability[5]);
    capability[7] = 1U; /* USB transport revision */
    capability[8] = 0U;
    *data = capability;
    /* *length is an output: the stack passes 0 in and the handler states how much it returns.
     * Clamping against the incoming value answers every request with an empty report, which
     * Windows surfaces as a failed HidD_GetFeature. */
    *length = MIN((int32_t)sizeof(capability), (int32_t)setup->wLength);
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

static int send(uint8_t report_id, const uint8_t *payload, size_t length) {
    k_mutex_lock(&session_lock, K_FOREVER);
    const astrolabe_lease_t lease = session_lease;
    const int err = astrolabe_route_is_current(ASTROLABE_ROUTE_USB, lease)
                        ? queue_report(&data_queue, report_id, payload, length, lease)
                        : -ENOTCONN;
    k_mutex_unlock(&session_lock);
    return err;
}

static int send_rotation(const uint8_t payload[ASTROLABE_ROTATION_BYTES]) {
    return send(REPORT_ROTATION, payload, ASTROLABE_ROTATION_BYTES);
}

static int send_snapshot(const uint8_t payload[ASTROLABE_SNAPSHOT_BYTES]) {
    return send(REPORT_SNAPSHOT, payload, ASTROLABE_SNAPSHOT_BYTES);
}

static int usb_event_listener(const zmk_event_t *event) {
    const struct zmk_usb_conn_state_changed *changed = as_zmk_usb_conn_state_changed(event);
    if (changed == NULL) {
        return -ENOTSUP;
    }
    if (changed->conn_state == ZMK_USB_CONN_NONE || zmk_usb_get_status() == USB_DC_SUSPEND) {
        release_session();
        k_msgq_purge(&ack_queue);
        k_msgq_purge(&command_queue);
        atomic_clear(&tx_busy);
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(astrolabe_usb_listener, usb_event_listener);
ZMK_SUBSCRIPTION(astrolabe_usb_listener, zmk_usb_conn_state_changed);

static const struct astrolabe_transport usb_transport = {
    .send_rotation = send_rotation,
    .send_snapshot = send_snapshot,
    .revoked = revoked,
};

/*
 * Two couplings to ZMK a version bump can break silently:
 * - "_1" is the second HID instance; ZMK registers "_0" for its own keyboard/mouse interface, so
 *   CONFIG_USB_HID_DEVICE_COUNT must stay >= 2.
 * - This must register before ZMK calls usb_enable(): ZMK's USB init priorities are 94 and 95, so
 *   89 leaves headroom. If the vendor interface stops enumerating after a ZMK bump, check those.
 */
static int usb_init(void) {
    hid_device = device_get_binding(CONFIG_USB_HID_DEVICE_NAME "_1");
    if (hid_device == NULL) {
        LOG_ERR("second HID instance " CONFIG_USB_HID_DEVICE_NAME "_1 is not present");
        return -ENODEV;
    }
    k_work_init(&tx_work, tx_handler);
    k_work_init(&command_work, command_handler);
    k_work_init_delayable(&keepalive_work, keepalive_check);
    k_work_init_delayable(&bootloader_work, reboot_to_bootloader);
    astrolabe_route_register(ASTROLABE_ROUTE_USB, &usb_transport);
    usb_hid_register_device(hid_device, report_descriptor, sizeof(report_descriptor),
                            &hid_operations);
    return usb_hid_init(hid_device);
}

SYS_INIT(usb_init, APPLICATION, 89);
