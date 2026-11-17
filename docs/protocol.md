<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# The device protocol

Any device that speaks this protocol is an Astrolabe controller: the daemon will route its motion
into 3D apps and its buttons into bindings. The reference firmware implements it in
[`firmware/module/include/astrolabe/protocol.h`](../firmware/module/include/astrolabe/protocol.h);
change both together.

A controller sends two things:

- **Rotation** — three little-endian `float32` increments, `rx, ry, rz`, in radians, about the
  housing axes: +X right, +Y away from the user, +Z up (twist), right-handed. Radians, never sensor
  counts, so gains and thresholds survive a change of ball or sensor. Send the motion accumulated
  since the last packet, as often as the link allows; the reference firmware sends at most every
  7 ms.
- **Input snapshots** — the full pressed state of every control, never edges.

| Offset | Size | Meaning |
|---:|---:|---|
| 0 | 1 | Protocol version, `1` |
| 1 | 1 | Kind, `1` = input-state snapshot |
| 2 | 2 | Sequence, unsigned little-endian; increment on every change |
| 4 | 1 | State length `N` (1–32) |
| 5 | `N` | Pressed bitmask, least-significant bit first |

The daemon accepts a snapshot whose sequence is newer (`(incoming - previous) & 0xffff` in
`1..0x7fff`), and drops duplicates, stale packets, bad lengths, and bits the device's descriptor
does not declare. Because each snapshot is complete, the next one repairs anything a lost packet
missed. A new session accepts its first valid snapshot unconditionally.

## Owning the route

On its own a controller should behave like an ordinary mouse. When the daemon connects it **claims
the route**: from then on the device sends rotation and snapshots instead of HID pointer output,
until the daemon releases it, disconnects, or stops sending keepalives. Then the device must fall
back to being a mouse by itself — and release any button it was holding for the daemon — so a
crashed daemon never leaves a dead cursor.

## BLE

One primary service, `2cad0001-6e64-0146-b139-9cf2a4cd57fc`:

| Characteristic | UUID | Properties |
|---|---|---|
| Rotation | `2cad0002-…` | notify, 12 bytes |
| Input | `2cad0003-…` | notify, snapshot |
| Keepalive | `2cad0004-…` | read `[version=1, timeout_ms u16le]`; write anything |

(All four share the suffix `-6e64-0146-b139-9cf2a4cd57fc`.)

A client claims the route by subscribing to rotation or by writing the keepalive while subscribed,
and keeps it by writing the keepalive at least every `timeout / 3`. The keepalive is the important
part: a CCC subscription is stored in the bond and outlives the daemon that wrote it, so a
subscription alone is not evidence that anyone is listening. Notifications go only to the owning
connection. The standard Battery Service (`0x180F`/`0x2A19`) is optional; the daemon shows it when
present.

## USB

A vendor-defined HID interface, usage page `0xFF00`, usage `1`, beside the device's ordinary HID
interface. Report IDs; sizes exclude the ID byte:

| ID | Direction | Payload |
|---:|---|---|
| 1 | in | rotation, 12 bytes |
| 2 | in | snapshot, 6 bytes |
| 3 | out | command `[version=1, opcode, request_id u16le]` |
| 4 | feature | capability `[version=1, firmware_rev, report_mask, flags, keepalive_ms u16le, usb_rev=1, 0]` |
| 5 | in | ACK `[version=1, opcode, request_id u16le, result, owner]` |

Opcodes: attach `1`, detach `2`, keepalive `3`, reboot to UF2 bootloader `4`. Results: ok `0`,
forced standalone `1`, invalid `2`. Owners: standalone `0`, BLE `1`, USB `2`. Capability flags:
attach `bit0`, keepalive `bit1`, forced standalone `bit2`, bootloader command `bit3`. The device
answers attach with an ACK naming USB as owner, then sends a snapshot. USB takes priority over BLE.
Windows pads interrupt-in reads to the largest input report, so read reports by ID, not by length.

## Telling the daemon about a device

The protocol is fixed; what varies is how a device is found and what its controls are called. That
lives in a data-only descriptor under
[`trackball_daemon/devices/descriptor_data/`](../trackball_daemon/devices/descriptor_data/), checked
against [`device-descriptor-v1.schema.json`](../trackball_daemon/schemas/device-descriptor-v1.schema.json).
The reference device's, abridged:

```json
{
  "schema_version": 1,
  "device_id": "astrolabe_5way",
  "source_id": "ble.astrolabe",
  "label": "Astrolabe five-way switch",
  "match": {
    "advertised_names": ["Astrolabe"],
    "usb_hid": {"vendor_id": 7504, "product_id": 24926, "usage_page": 65280, "usage": 1,
                "product": "Astrolabe"}
  },
  "service_uuid": "2cad0001-6e64-0146-b139-9cf2a4cd57fc",
  "motion_characteristic": "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
  "input_characteristic": "2cad0003-6e64-0146-b139-9cf2a4cd57fc",
  "keepalive_characteristic": "2cad0004-6e64-0146-b139-9cf2a4cd57fc",
  "controls": [
    {"id": "fiveway.up", "label": "Astrolabe Up", "bit": 0},
    {"id": "fiveway.center", "label": "Astrolabe Center", "bit": 4}
  ]
}
```

Controls become binding tokens `source_id:control_id`, such as `ble.astrolabe:fiveway.center`.
Descriptors are data, never code: unknown keys are rejected, and a device with a different wire
protocol needs reviewed adapter code rather than a descriptor.

The daemon matches a BLE device by its GATT layout, with the advertised name only to break ties. It
remembers the address of the first device it reaches, because Windows stops advertising a device it
has paired as a mouse, and a scan cannot find it again. USB matching needs all five `usb_hid` fields.

The current USB IDs, `0x1D50:0x615E`, are ZMK's development pair shared by every ZMK board; a
pid.codes allocation is a public-release gate in [`TODO.md`](../TODO.md).
