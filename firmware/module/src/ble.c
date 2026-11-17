/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * BLE daemon transport: one GATT service carrying rotation, input snapshots, and a keepalive.
 *
 * A connection owns the route while it is subscribed to rotation and keeps writing the keepalive.
 * Subscribing (a CCC write) or writing the keepalive claims the route. The keepalive is what makes
 * ownership mean "a daemon is alive" rather than "a daemon once was": a CCC is persisted in the
 * bond, and Windows keeps the link up for the HID mouse, so a daemon that dies without
 * unsubscribing would otherwise leave standalone output suppressed across reconnects and reboots.
 * The claim expires without keepalives, and the route falls back to standalone on its own.
 */

#include "core.h"

#include <errno.h>

#include <zephyr/bluetooth/att.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>

LOG_MODULE_REGISTER(astrolabe_ble, CONFIG_ASTROLABE_LOG_LEVEL);

/* Longer than USB's 1500 ms: a Python client over BLE has more jitter to absorb. The daemon reads
 * this value and paces itself at a third of it, so this is the only place the number lives. */
#define KEEPALIVE_TIMEOUT_MS 2000U

/* Indices into the service's attribute table; the notify paths address values through them. */
enum {
    ATTR_SERVICE,
    ATTR_ROTATION_DECLARATION,
    ATTR_ROTATION_VALUE,
    ATTR_ROTATION_CCC,
    ATTR_INPUT_DECLARATION,
    ATTR_INPUT_VALUE,
    ATTR_INPUT_CCC,
    ATTR_KEEPALIVE_DECLARATION,
    ATTR_KEEPALIVE_VALUE,
};

extern const struct bt_gatt_service_static astrolabe_service;

/* The owning connection holds a reference while set. A claim in flight is owner set with lease
 * NONE: the slot is reserved so a second connection cannot race it. */
static K_MUTEX_DEFINE(owner_lock);
static struct bt_conn *owner;
static astrolabe_lease_t owner_lease;
static uint32_t keepalive_deadline;

static void keepalive_check(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(keepalive_work, keepalive_check);
static void snapshot_publish(struct k_work *work);
static K_WORK_DEFINE(snapshot_work, snapshot_publish);

/* A referenced owner whose lease the route still honours, or NULL. */
static struct bt_conn *current_owner(astrolabe_lease_t *lease) {
    struct bt_conn *conn = NULL;
    astrolabe_lease_t held = ASTROLABE_LEASE_NONE;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner != NULL && owner_lease != ASTROLABE_LEASE_NONE) {
        conn = bt_conn_ref(owner);
        held = owner_lease;
    }
    k_mutex_unlock(&owner_lock);
    if (conn != NULL && !astrolabe_route_is_current(ASTROLABE_ROUTE_BLE, held)) {
        bt_conn_unref(conn);
        conn = NULL;
    }
    if (lease != NULL) {
        *lease = held;
    }
    return conn;
}

/* Forget the owner if it is `conn` holding `lease`; NULL `conn` matches any connection. */
static void forget(struct bt_conn *conn, astrolabe_lease_t lease) {
    struct bt_conn *dropped = NULL;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner != NULL && (conn == NULL || owner == conn) && owner_lease == lease) {
        dropped = owner;
        owner = NULL;
        owner_lease = ASTROLABE_LEASE_NONE;
    }
    k_mutex_unlock(&owner_lock);
    if (dropped != NULL) {
        bt_conn_unref(dropped);
    }
}

static void revoked(astrolabe_lease_t lease) { forget(NULL, lease); }

static void arm_keepalive(void) {
    k_mutex_lock(&owner_lock, K_FOREVER);
    keepalive_deadline = k_uptime_get_32() + KEEPALIVE_TIMEOUT_MS;
    k_mutex_unlock(&owner_lock);
    k_work_reschedule(&keepalive_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

static void release(struct bt_conn *conn) {
    k_mutex_lock(&owner_lock, K_FOREVER);
    const bool mine = owner == conn;
    const astrolabe_lease_t lease = owner_lease;
    k_mutex_unlock(&owner_lock);
    if (!mine) {
        return;
    }
    if (lease != ASTROLABE_LEASE_NONE) {
        astrolabe_route_release(ASTROLABE_ROUTE_BLE, lease); /* revokes, which forgets */
    }
    forget(conn, lease);
}

static int claim(struct bt_conn *conn) {
    if (astrolabe_route_forced_standalone()) {
        return -EPERM;
    }

    k_mutex_lock(&owner_lock, K_FOREVER);
    const bool mine = owner == conn;
    const bool reserved = owner != NULL;
    const astrolabe_lease_t held = owner_lease;
    k_mutex_unlock(&owner_lock);

    if (mine && held != ASTROLABE_LEASE_NONE) {
        if (astrolabe_route_is_current(ASTROLABE_ROUTE_BLE, held)) {
            arm_keepalive();
            return 0;
        }
        forget(conn, held); /* stale: the route moved on without telling us */
    } else if (reserved) {
        return -EBUSY;
    }

    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner != NULL) {
        k_mutex_unlock(&owner_lock);
        return -EBUSY;
    }
    owner = bt_conn_ref(conn);
    owner_lease = ASTROLABE_LEASE_NONE;
    k_mutex_unlock(&owner_lock);

    astrolabe_lease_t lease;
    const int err = astrolabe_route_claim(ASTROLABE_ROUTE_BLE, &lease);
    if (err != 0) {
        forget(conn, ASTROLABE_LEASE_NONE);
        return err;
    }

    k_mutex_lock(&owner_lock, K_FOREVER);
    const bool installed = owner == conn && owner_lease == ASTROLABE_LEASE_NONE;
    if (installed) {
        owner_lease = lease;
    }
    k_mutex_unlock(&owner_lock);
    if (!installed || !astrolabe_route_is_current(ASTROLABE_ROUTE_BLE, lease)) {
        astrolabe_route_release(ASTROLABE_ROUTE_BLE, lease);
        forget(conn, installed ? lease : ASTROLABE_LEASE_NONE);
        return -EBUSY;
    }
    /* The clock starts at the claim, so a subscription with no daemon behind it expires too. */
    arm_keepalive();
    return 0;
}

static ssize_t claim_result(int err, ssize_t success) {
    switch (err) {
    case 0:
        return success;
    case -EPERM:
        return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    case -EBUSY:
        return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
    default:
        return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    }
}

static void keepalive_check(struct k_work *work) {
    ARG_UNUSED(work);
    astrolabe_lease_t lease;
    struct bt_conn *conn = current_owner(&lease);
    if (conn == NULL) {
        return;
    }
    k_mutex_lock(&owner_lock, K_FOREVER);
    const uint32_t deadline = keepalive_deadline;
    k_mutex_unlock(&owner_lock);

    if ((int32_t)(k_uptime_get_32() - deadline) >= 0) {
        LOG_WRN("BLE daemon keepalive expired, releasing the route");
        release(conn);
    } else {
        k_work_reschedule(&keepalive_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
    }
    bt_conn_unref(conn);
}

static void snapshot_publish(struct k_work *work) {
    ARG_UNUSED(work);
    astrolabe_lease_t lease;
    struct bt_conn *conn = current_owner(&lease);
    if (conn != NULL) {
        bt_conn_unref(conn);
        (void)astrolabe_route_publish_snapshot(ASTROLABE_ROUTE_BLE, lease);
    }
}

static ssize_t keepalive_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
                              uint16_t len, uint16_t offset) {
    uint8_t value[3] = {ASTROLABE_PROTOCOL_VERSION};
    sys_put_le16(KEEPALIVE_TIMEOUT_MS, &value[1]);
    return bt_gatt_attr_read(conn, attr, buf, len, offset, value, sizeof(value));
}

static ssize_t keepalive_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                               const void *buf, uint16_t len, uint16_t offset, uint8_t flags) {
    ARG_UNUSED(attr);
    ARG_UNUSED(buf);
    ARG_UNUSED(flags);
    if (offset != 0U) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }
    /* A subscription restored from the bond never writes the CCC, so the first keepalive is what
     * claims for it -- but only for a client that will actually receive the notifications. */
    if (!bt_gatt_is_subscribed(conn, &astrolabe_service.attrs[ATTR_ROTATION_VALUE],
                               BT_GATT_CCC_NOTIFY)) {
        return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    }
    return claim_result(claim(conn), len);
}

static ssize_t rotation_ccc_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                                  uint16_t value) {
    ARG_UNUSED(attr);
    if (value == 0U) {
        release(conn);
        return sizeof(value);
    }
    if (value != BT_GATT_CCC_NOTIFY) {
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }
    return claim_result(claim(conn), sizeof(value));
}

static ssize_t input_ccc_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                               uint16_t value) {
    ARG_UNUSED(conn);
    ARG_UNUSED(attr);
    if (value != 0U && value != BT_GATT_CCC_NOTIFY) {
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }
    if (value == BT_GATT_CCC_NOTIFY) {
        k_work_submit(&snapshot_work); /* the owner starts from the full control state */
    }
    return sizeof(value);
}

/* Notifications go to the owner only. */
static bool owner_match(struct bt_conn *conn, const struct bt_gatt_attr *attr) {
    ARG_UNUSED(attr);
    struct bt_conn *current = current_owner(NULL);
    const bool match = current == conn;
    if (current != NULL) {
        bt_conn_unref(current);
    }
    return match;
}

static struct _bt_gatt_ccc rotation_ccc =
    BT_GATT_CCC_INITIALIZER(NULL, rotation_ccc_write, owner_match);
static struct _bt_gatt_ccc input_ccc = BT_GATT_CCC_INITIALIZER(NULL, input_ccc_write, owner_match);

BT_GATT_SERVICE_DEFINE(
    astrolabe_service, BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(ASTROLABE_UUID_SERVICE)),
    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(ASTROLABE_UUID_ROTATION), BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE, NULL, NULL, NULL),
    BT_GATT_CCC_MANAGED(&rotation_ccc, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(ASTROLABE_UUID_INPUT), BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE, NULL, NULL, NULL),
    BT_GATT_CCC_MANAGED(&input_ccc, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(ASTROLABE_UUID_KEEPALIVE),
                           BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE |
                               BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                           BT_GATT_PERM_READ | BT_GATT_PERM_WRITE, keepalive_read,
                           keepalive_write, NULL));

static int notify(int attr_index, const uint8_t *payload, uint16_t length) {
    struct bt_conn *conn = current_owner(NULL);
    if (conn == NULL) {
        return -ENOTCONN;
    }
    const struct bt_gatt_attr *attr = &astrolabe_service.attrs[attr_index];
    const int err = bt_gatt_is_subscribed(conn, attr, BT_GATT_CCC_NOTIFY)
                        ? bt_gatt_notify(conn, attr, payload, length)
                        : -EACCES;
    bt_conn_unref(conn);
    return err;
}

static int send_rotation(const uint8_t payload[ASTROLABE_ROTATION_BYTES]) {
    return notify(ATTR_ROTATION_VALUE, payload, ASTROLABE_ROTATION_BYTES);
}

static int send_snapshot(const uint8_t payload[ASTROLABE_SNAPSHOT_BYTES]) {
    return notify(ATTR_INPUT_VALUE, payload, ASTROLABE_SNAPSHOT_BYTES);
}

static void disconnected(struct bt_conn *conn, uint8_t reason) {
    ARG_UNUSED(reason);
    release(conn);
}

BT_CONN_CB_DEFINE(astrolabe_conn_callbacks) = {
    .disconnected = disconnected,
};

static const struct astrolabe_transport ble_transport = {
    .send_rotation = send_rotation,
    .send_snapshot = send_snapshot,
    .revoked = revoked,
};

static int ble_init(void) {
    astrolabe_route_register(ASTROLABE_ROUTE_BLE, &ble_transport);
    return 0;
}

SYS_INIT(ble_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
