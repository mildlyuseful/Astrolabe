/*
 * SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 */

#include <astrolabe/protocol.h>
#include <astrolabe/route.h>

#include <errno.h>

#include <zephyr/bluetooth/att.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>

LOG_MODULE_REGISTER(astrolabe_gatt, CONFIG_ASTROLABE_LOG_LEVEL);

#define ASTROLABE_SERVICE_UUID                                                                     \
    BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(0x2cad0001, 0x6e64, 0x0146, 0xb139, 0x9cf2a4cd57fc))
#define ASTROLABE_ROTATION_UUID                                                                    \
    BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(0x2cad0002, 0x6e64, 0x0146, 0xb139, 0x9cf2a4cd57fc))
#define ASTROLABE_INPUT_UUID                                                                       \
    BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(0x2cad0003, 0x6e64, 0x0146, 0xb139, 0x9cf2a4cd57fc))
#define ASTROLABE_KEEPALIVE_UUID                                                                   \
    BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(0x2cad0004, 0x6e64, 0x0146, 0xb139, 0x9cf2a4cd57fc))

/* Appended, never inserted: the notify paths index this to find their value attributes. */
enum attribute_index {
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

/* Longer than USB's 1500 ms because a Python client over BLE has more jitter to absorb, and short
 * enough that a stale claim clears itself before it reads as broken. The daemon paces at a third of
 * whatever it reads here, so this is the only place the number lives. */
#define KEEPALIVE_TIMEOUT_MS 2000U

static struct k_mutex owner_lock;
static struct bt_conn *owner;
static astrolabe_route_lease_t owner_lease;
static struct k_work_delayable restore_work;
static struct k_work_delayable ownership_work;
static uint32_t keepalive_deadline;

static bool connection_is_owner(struct bt_conn *conn) {
    astrolabe_route_lease_t lease = ASTROLABE_ROUTE_LEASE_NONE;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner == conn) {
        lease = owner_lease;
    }
    k_mutex_unlock(&owner_lock);
    return astrolabe_route_lease_is_current(ASTROLABE_ROUTE_BLE_DAEMON, lease);
}

static astrolabe_route_lease_t connection_lease(struct bt_conn *conn) {
    astrolabe_route_lease_t lease = ASTROLABE_ROUTE_LEASE_NONE;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner == conn) {
        lease = owner_lease;
    }
    k_mutex_unlock(&owner_lock);
    return lease;
}

static void clear_local_owner(struct bt_conn *conn, astrolabe_route_lease_t lease) {
    struct bt_conn *released = NULL;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner == conn && owner_lease == lease) {
        released = owner;
        owner = NULL;
        owner_lease = ASTROLABE_ROUTE_LEASE_NONE;
    }
    k_mutex_unlock(&owner_lock);
    if (released != NULL) {
        bt_conn_unref(released);
    }
}

static void clear_owner(struct bt_conn *conn) {
    const astrolabe_route_lease_t lease = connection_lease(conn);
    if (lease == ASTROLABE_ROUTE_LEASE_NONE) {
        clear_local_owner(conn, ASTROLABE_ROUTE_LEASE_NONE);
        return;
    }
    astrolabe_route_release(ASTROLABE_ROUTE_BLE_DAEMON, lease);
    clear_local_owner(conn, lease);
}

void astrolabe_gatt_route_revoked(astrolabe_route_lease_t lease) {
    struct bt_conn *released = NULL;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner != NULL && owner_lease == lease) {
        released = owner;
        owner = NULL;
        owner_lease = ASTROLABE_ROUTE_LEASE_NONE;
    }
    k_mutex_unlock(&owner_lock);
    if (released != NULL) {
        bt_conn_unref(released);
    }
}

void astrolabe_gatt_route_available(void) {
    k_work_reschedule(&restore_work, K_MSEC(100));
}

static struct bt_conn *owner_ref(astrolabe_route_lease_t *lease);

static void arm_keepalive(void) {
    keepalive_deadline = k_uptime_get_32() + KEEPALIVE_TIMEOUT_MS;
    k_work_reschedule(&ownership_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

/*
 * The fail-safe that makes a subscription mean "a daemon is alive" rather than "a daemon once was".
 *
 * A CCC is persisted into the bond, and the link survives the daemon: Windows holds it open for the
 * HID mouse, so a daemon that dies without unsubscribing leaves the route claimed with no
 * disconnect to notice. Worse, the claim is restored on every reconnect, so the state outlives a
 * power cycle and only a reflash or forgetting the bond clears it -- with the cursor and every
 * gesture gone, because gestures live on the standalone layer.
 */
static void ownership_handler(struct k_work *work) {
    ARG_UNUSED(work);

    astrolabe_route_lease_t lease;
    struct bt_conn *conn = owner_ref(&lease);
    if (conn == NULL) {
        keepalive_deadline = 0U;
        return;
    }

    const uint32_t deadline = keepalive_deadline;
    if (deadline != 0U && (int32_t)(k_uptime_get_32() - deadline) >= 0) {
        LOG_WRN("BLE daemon keepalive expired, releasing the route");
        keepalive_deadline = 0U;
        clear_owner(conn);
        bt_conn_unref(conn);
        return;
    }

    bt_conn_unref(conn);
    k_work_reschedule(&ownership_work, K_MSEC(KEEPALIVE_TIMEOUT_MS / 3U));
}

static ssize_t keepalive_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
                              uint16_t len, uint16_t offset) {
    uint8_t value[3] = {ASTROLABE_PROTOCOL_VERSION, 0U, 0U};
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
    /* Only the owner may refresh. A non-owner writing here must not extend someone else's lease,
     * and telling it so is what lets the daemon notice it has lost the route. */
    if (!connection_is_owner(conn)) {
        return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    }
    arm_keepalive();
    return len;
}

static ssize_t rotation_ccc_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                                  uint16_t value) {
    ARG_UNUSED(attr);
    if (value == 0U) {
        clear_owner(conn);
        return sizeof(value);
    }
    if (value != BT_GATT_CCC_NOTIFY) {
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }
    if (astrolabe_route_forced_standalone()) {
        return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    }

    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner == conn && owner_lease != ASTROLABE_ROUTE_LEASE_NONE) {
        const astrolabe_route_lease_t lease = owner_lease;
        k_mutex_unlock(&owner_lock);
        if (astrolabe_route_lease_is_current(ASTROLABE_ROUTE_BLE_DAEMON, lease)) {
            return sizeof(value);
        }
        clear_local_owner(conn, lease);
        k_mutex_lock(&owner_lock, K_FOREVER);
    }
    if (owner != NULL) {
        k_mutex_unlock(&owner_lock);
        return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
    }
    owner = bt_conn_ref(conn);
    owner_lease = ASTROLABE_ROUTE_LEASE_NONE;
    k_mutex_unlock(&owner_lock);

    astrolabe_route_lease_t lease;
    const int err = astrolabe_route_claim(ASTROLABE_ROUTE_BLE_DAEMON, &lease);
    if (err != 0) {
        clear_local_owner(conn, ASTROLABE_ROUTE_LEASE_NONE);
        return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    }

    k_mutex_lock(&owner_lock, K_FOREVER);
    const bool installed = owner == conn && owner_lease == ASTROLABE_ROUTE_LEASE_NONE;
    if (installed) {
        owner_lease = lease;
    }
    k_mutex_unlock(&owner_lock);
    if (!installed || !astrolabe_route_lease_is_current(ASTROLABE_ROUTE_BLE_DAEMON, lease)) {
        astrolabe_route_release(ASTROLABE_ROUTE_BLE_DAEMON, lease);
        clear_local_owner(conn, installed ? lease : ASTROLABE_ROUTE_LEASE_NONE);
        return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    }
    /* Starts the clock on a subscription restored from a bond as much as on a fresh one -- that is
     * the case with no daemon behind it, so it is the one that must expire. */
    arm_keepalive();
    return sizeof(value);
}

static ssize_t input_ccc_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                               uint16_t value) {
    ARG_UNUSED(attr);
    if (value == 0U) {
        return sizeof(value);
    }
    if (value != BT_GATT_CCC_NOTIFY) {
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }
    if (!connection_is_owner(conn)) {
        return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
    }
    k_work_reschedule(&restore_work, K_NO_WAIT);
    return sizeof(value);
}

static bool owner_match(struct bt_conn *conn, const struct bt_gatt_attr *attr) {
    ARG_UNUSED(attr);
    return connection_is_owner(conn);
}

static struct _bt_gatt_ccc rotation_ccc =
    BT_GATT_CCC_INITIALIZER(NULL, rotation_ccc_write, owner_match);
static struct _bt_gatt_ccc input_ccc = BT_GATT_CCC_INITIALIZER(NULL, input_ccc_write, owner_match);

BT_GATT_SERVICE_DEFINE(astrolabe_service, BT_GATT_PRIMARY_SERVICE(ASTROLABE_SERVICE_UUID),
                       BT_GATT_CHARACTERISTIC(ASTROLABE_ROTATION_UUID, BT_GATT_CHRC_NOTIFY,
                                              BT_GATT_PERM_NONE, NULL, NULL, NULL),
                       BT_GATT_CCC_MANAGED(&rotation_ccc, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
                       BT_GATT_CHARACTERISTIC(ASTROLABE_INPUT_UUID, BT_GATT_CHRC_NOTIFY,
                                              BT_GATT_PERM_NONE, NULL, NULL, NULL),
                       BT_GATT_CCC_MANAGED(&input_ccc, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
                       BT_GATT_CHARACTERISTIC(ASTROLABE_KEEPALIVE_UUID,
                                              BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE |
                                                  BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                                              BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
                                              keepalive_read, keepalive_write, NULL));

static struct bt_conn *owner_ref(astrolabe_route_lease_t *lease) {
    struct bt_conn *conn = NULL;
    k_mutex_lock(&owner_lock, K_FOREVER);
    if (owner != NULL && owner_lease != ASTROLABE_ROUTE_LEASE_NONE) {
        conn = bt_conn_ref(owner);
        *lease = owner_lease;
    }
    k_mutex_unlock(&owner_lock);
    if (conn != NULL && !astrolabe_route_lease_is_current(ASTROLABE_ROUTE_BLE_DAEMON, *lease)) {
        bt_conn_unref(conn);
        conn = NULL;
    }
    return conn;
}

int astrolabe_gatt_rotation(const uint8_t payload[12]) {
    astrolabe_route_lease_t lease;
    struct bt_conn *conn = owner_ref(&lease);
    if (conn == NULL) {
        return -ENOTCONN;
    }
    int err = -EACCES;
    if (bt_gatt_is_subscribed(conn, &astrolabe_service.attrs[ATTR_ROTATION_VALUE],
                              BT_GATT_CCC_NOTIFY)) {
        err = bt_gatt_notify(conn, &astrolabe_service.attrs[ATTR_ROTATION_VALUE], payload,
                             ASTROLABE_ROTATION_PACKET_BYTES);
    }
    bt_conn_unref(conn);
    return err;
}

int astrolabe_gatt_snapshot(const uint8_t payload[6]) {
    astrolabe_route_lease_t lease;
    struct bt_conn *conn = owner_ref(&lease);
    if (conn == NULL) {
        return -ENOTCONN;
    }
    int err = -EACCES;
    if (bt_gatt_is_subscribed(conn, &astrolabe_service.attrs[ATTR_INPUT_VALUE],
                              BT_GATT_CCC_NOTIFY)) {
        err = bt_gatt_notify(conn, &astrolabe_service.attrs[ATTR_INPUT_VALUE], payload,
                             ASTROLABE_INPUT_PACKET_BYTES);
    }
    bt_conn_unref(conn);
    return err;
}

static void restore_candidate(struct bt_conn *conn, void *user_data) {
    ARG_UNUSED(user_data);
    k_mutex_lock(&owner_lock, K_FOREVER);
    const bool available = owner == NULL;
    k_mutex_unlock(&owner_lock);
    if (!available || astrolabe_route_forced_standalone() ||
        !bt_gatt_is_subscribed(conn, &astrolabe_service.attrs[ATTR_ROTATION_VALUE],
                               BT_GATT_CCC_NOTIFY)) {
        return;
    }

    (void)rotation_ccc_write(conn, &astrolabe_service.attrs[ATTR_ROTATION_CCC], BT_GATT_CCC_NOTIFY);
}

static void restore_handler(struct k_work *work) {
    ARG_UNUSED(work);
    bt_conn_foreach(BT_CONN_TYPE_LE, restore_candidate, NULL);
    astrolabe_route_lease_t lease;
    struct bt_conn *conn = owner_ref(&lease);
    if (conn != NULL) {
        bt_conn_unref(conn);
        (void)astrolabe_route_publish_snapshot(ASTROLABE_ROUTE_BLE_DAEMON, lease);
    }
}

static void connected(struct bt_conn *conn, uint8_t err) {
    ARG_UNUSED(conn);
    if (err == 0U) {
        k_work_reschedule(&restore_work, K_MSEC(100));
    }
}

static void disconnected(struct bt_conn *conn, uint8_t reason) {
    ARG_UNUSED(reason);
    clear_owner(conn);
}

BT_CONN_CB_DEFINE(astrolabe_conn_callbacks) = {
    .connected = connected,
    .disconnected = disconnected,
};

static int gatt_init(void) {
    k_mutex_init(&owner_lock);
    owner_lease = ASTROLABE_ROUTE_LEASE_NONE;
    k_work_init_delayable(&restore_work, restore_handler);
    k_work_init_delayable(&ownership_work, ownership_handler);
    return 0;
}

SYS_INIT(gatt_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
