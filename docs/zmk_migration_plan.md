# ZMK production-firmware migration record

This is the current design and transition record for the P4 ZMK production-firmware epic.
[`../TODO.md`](../TODO.md) owns every unclosed hardware, live-transport, measurement, and release
gate. Code under [`../firmware/zmk/`](../firmware/zmk/) is now the production-firmware candidate;
successful automated builds establish source and toolchain integration, not qualification of a
physical product.

The Arduino sketches remain compatibility and diagnostic references until the live matrices below
are complete. Do not delete the placeholder or replace the existing protocol-bench release gate
merely because the ZMK candidate compiles.

## Implemented outcome

| Slice | Current result | Evidence boundary |
|---|---|---|
| Reproducible inputs | ZMK and Zephyr are pinned to exact commits; CI uses an immutable ZMK build image and retains the frozen west manifest, build configuration, DTS, ELF, UF2, and hashes. | Establishes reconstructible inputs and buildability, not bit-for-bit reproducibility. |
| Sensor and standalone route | One out-of-tree Zephyr device owns the shared three-wire bus, both PMW3610s, the dual-sensor solver, and standalone cursor/scroll output. | Exercised on the prototype fixture: pointer delivery at 133 Hz with daemon feel parity, all four gestures, and clean route-driven layer transitions. Final-assembly behavior is unverified, and the ball diameter has since changed. |
| BLE daemon route | The frozen GATT UUIDs and v1 rotation/input packets are emitted without changing the daemon binding namespace. | Source and daemon protocol tests pass; live ZMK-on-device BLE remains open. |
| Wired daemon route | A second USB HID interface implements capability discovery, acknowledged ownership commands, rotation, and full input snapshots. | Firmware and daemon paths build/test; enumeration, cable-pull, suspend, and handover need real Windows hardware. |
| Ownership safety | Firmware has one route owner; the daemon uses identity leases so callbacks and teardown from an old BLE or USB session cannot mutate a replacement session. | Automated lifecycle coverage only. |

## Fixed boundaries

| Concern | Decision |
|---|---|
| Production hardware | Frozen in [`hardware.md`](hardware.md): SuperMini nRF52840, two PMW3610 on a 52 mm ball, ALPS SKRHADE010 five-way |
| ZMK target | Upstream `nice_nano_v2`; no in-tree board fork |
| Firmware stack | Exact ZMK and Zephyr commits plus one out-of-tree Astrolabe module; no ZMK patch |
| BLE payloads | Frozen v1 rotation and full-state input snapshot contract, byte-identical to the existing daemon protocol |
| Wired behavior | Normal ZMK HID mouse when standalone, plus a separate vendor HID interface for daemon data and ownership |
| Responsibility | Firmware owns acquisition, fusion, power integration, bonding, local HID, route state, and keymap; the daemon owns bindings, application context, and navigation state |
| Binding identity | `source_id` remains `ble.astrolabe` for BLE and USB, preserving stored `ble.astrolabe:fiveway.*` tokens |

Freezing the component selection does not qualify the assembly. Sensor placement, wiring harness,
switch mechanics, power path, and enclosure behavior still require the physical matrix in
[`../TODO.md`](../TODO.md).

## Critical changes from the original proposal

Implementation review corrected several assumptions before they became contracts:

- Zephyr is pinned explicitly, not only transitively through ZMK. The source manifest is
  [`../firmware/zmk/config/west.yml`](../firmware/zmk/config/west.yml), and CI retains the fully
  frozen imported manifest.
- The PMW3610 code is a first-party Apache-2.0 port of this repository's current prototype, not a
  community-derived driver. It preserves automatic Run/Rest (`FMODE=0`), burst-latched 12-bit
  deltas, exact bus timing, and diagnostic SQUAL/shutter capture. Neither the prototype nor this
  port applies an SQUAL validity filter or a production wake-settle discard, so those behaviors are
  not claimed as validated mitigations.
- A composite device owns acquisition and fusion directly. Zephyr's ordinary SPI and independent
  sensor-device model cannot express the atomic chip-select-framed reads required by two sensors on
  one shared half-duplex SDIO line without adding more coordination surface.
- A route-aware `&astro` behavior maintains one physical bitset and decides locally whether to emit
  a standalone button or a daemon snapshot. A daemon-only keymap layer was rejected at first,
  because changing layers while a key is held can send its release to a different behavior — then
  adopted anyway once standalone needed gestures that must not sit in front of a daemon control
  bit. The hazard is real and was accepted, not solved: Down, Right, and Center bind the same
  `&astro` on both layers, so the three positions that carry buttons cannot split a press from its
  release, and the route's own transition path releases emitted standalone buttons on claim and
  suppresses fallback clicks for already-held controls on release. Up and Left do differ between
  layers; a route change landing mid-gesture there is an open verification gate in
  [`../TODO.md`](../TODO.md).
- USB commands carry a 16-bit request ID and receive a matching ACK. A flag-only attach report
  could not distinguish an accepted claim from a stale or rejected command during handover.
- Supported BLE-to-USB preference is coordinated by the daemon: receive a successful USB attach
  ACK, replace the provider lease, then pause and tear down BLE. Arbitrary competing owners on two
  hosts are not a supported transport topology.

## Repository layout

```text
firmware/zmk/
  config/
    west.yml
    astrolabe.conf
    astrolabe.keymap
    logging.conf
  module/
    zephyr/module.yml
    boards/shields/astrolabe/
    dts/bindings/
    drivers/pmw3610/pmw3610.c
    include/astrolabe/
    src/
```

Pins, sensor poses, frame tilt, ball diameter, CPI, polling cadence, and cursor/scroll constants are
shield data, which keeps a hardware revision reviewable as data rather than as driver edits. The
values are the frozen contract in [`hardware.md`](hardware.md); they are transcribed from the
validation prototype except the ball diameter, which is the shipped 52 mm.

## Firmware data flow

```text
shared PMW3610 bus + MOTION interrupts
  -> burst-latched left/right samples
  -> least-squares fusion in the configured housing frame
  -> one output route
       standalone -> yaw-gated scroll or cursor input -> ordinary ZMK HID endpoint
       BLE daemon  -> frozen rotation/input values -> custom GATT service
       USB daemon  -> same values -> vendor HID input reports
```

The standalone mapping is Down=left click, Right=right click, Center=middle click. Up and Left carry
no button and instead host four gestures — double-tap for output toggle and next BLE profile, three-
second hold for bootloader and bond clear — built from stock ZMK tap-dance and hold-tap. They live
only on the standalone layer, because a tap-dance must wait out its term before it can know a tap
was single, and paying that latency on a daemon control bit is the mistake the original combo
arbitration made. Reset is deliberately unbound; the hardware power switch covers it. Holding Center
through boot samples a forced-standalone escape that rejects daemon claims until reboot.

### Route invariants

- Boot always starts standalone; route ownership is never persisted.
- A standalone-to-daemon claim releases every emitted standalone HID button and clears accumulated
  motion before changing the route.
- A daemon release returns to standalone but suppresses fallback clicks for controls already held;
  each becomes eligible only after its physical release.
- Every accepted control transition advances the 16-bit input sequence. A new daemon session opens
  with a complete snapshot, so missed edges repair at the next accepted full state.
- Motion is removed from an accumulator only after its selected transport accepts the report.
- BLE and USB have one firmware owner at a time. USB may replace BLE; a BLE claim is rejected while
  a USB lease is live. A release only acts when it still owns that route.

### Sensor and power policy

The PMW3610 pair remains in its automatic Run/Rest policy. A MOTION or button interrupt opens the
configured active polling window; after it closes, the MCU waits for another interrupt. Device
suspend cancels polling and resume reopens the active window without resetting or forcing a sensor
performance mode.

SQUAL and shutter are parsed as diagnostic telemetry only. Physical sleep/wake, first-delta,
surface-loss, illumination, and battery-current testing must decide whether any additional validity
rule is justified. No magnitude cap or unearned filter is included.

ZMK owns system idle/deep sleep, bonding, host profiles, endpoint selection, UF2 recovery, VDDH
battery sensing, and the standard BLE Battery Service. Battery behavior while charging or powered
over USB remains a physical verification gate; the daemon labels an active USB session as externally
powered instead of presenting a fresh USB-derived percentage.

## Transport contracts

### BLE: frozen v1

The service and characteristics remain:

- service `2cad0001-6e64-0146-b139-9cf2a4cd57fc`;
- rotation `2cad0002-6e64-0146-b139-9cf2a4cd57fc`;
- input state `2cad0003-6e64-0146-b139-9cf2a4cd57fc`.

Rotation is three little-endian `float32` values. Input is
`[version=1, kind=1, sequence_le16, state_bytes=1, bitset]`. Rotation notification ownership is
connection-specific; unsubscribe or disconnect releases only that owner. The input CCC is accepted
only for the rotation owner. Persisted CCC restoration reclaims an available route and publishes a
fresh snapshot. USB release also schedules that restoration, so an accepted USB attach whose ACK
was lost cannot strand an otherwise live BLE subscription.

The existing name-based daemon scan remains supported. The custom service UUID is not added to
ZMK's advertising payload by this module, so service-UUID-only discovery is not claimed.

### USB vendor HID revision 1

The vendor collection uses usage page `0xFF00`, usage `0x0001`, on a second HID instance. Report
payload sizes below exclude the report-ID byte.

| ID | Direction | Payload |
|---:|---|---|
| 1 | Input | 12-byte frozen rotation value |
| 2 | Input | 6-byte frozen v1 input snapshot |
| 3 | Output | `[version, opcode, request_id_le16]` |
| 4 | Feature | `[version, firmware_revision, report_mask, flags, keepalive_ms_le16, usb_revision, reserved]` |
| 5 | Input | `[version, opcode, request_id_le16, result, owner]` |

Command opcodes are attach=1, detach=2, and keepalive=3. Result codes are success=0,
forced-standalone=1, and invalid=2. Owner values are standalone=0, BLE=1, and USB=2. The current
keepalive timeout is 1500 ms. Attach queues the matching ACK before the opening full snapshot;
timeout, cable loss, endpoint failure, detach, or ZMK USB-disconnected state releases USB ownership.
The listener also checks the underlying USB status for suspend when ZMK emits a connection-state
event. ZMK coalesces the public suspend state into HID readiness, so immediate transient-suspend
detection is not claimed; failed writes and the keepalive deadline remain the independent fallback.

The descriptor match additionally requires the product string `Astrolabe`. Development builds use
the upstream ZMK VID/PID and must not be represented as release identity. Obtain an assigned VID/PID
and update the descriptor before distributing production hardware.

## Daemon transport and handover

The device descriptor carries an optional USB HID match beside the existing BLE identity. The
field is additive, so existing descriptors and rotation-only adapters remain valid without a
schema bump. Both transports feed the same `MotionSample` decoder and
`SnapshotInputProvider`.

Every provider session returns an opaque identity lease. Notification callbacks, rotation
publication, input snapshots, battery updates, and disconnects act only if their captured lease is
still current. USB preference follows this order:

1. Discover the exact vendor interface and validate its feature capability.
2. Send attach with a new request ID and wait for the matching successful ACK naming USB as owner.
3. Begin the USB provider session, invalidating every callback captured by the BLE session.
4. Clear the shared BLE-enabled event; the BLE loop unsubscribes and disconnects without reconnect
   status churn.
5. On USB loss, release the exact USB lease, mark external power absent, then re-enable BLE.

This ordering prevents duplicate input delivery and prevents a late BLE teardown from releasing a
new USB session. Firmware keepalive is the independent fail-safe if the daemon terminates without a
detach.

## Build and provenance

The manifest pins ZMK `edf5c0814fd3ea202e43aad2d68fd32e882a518c` and Zephyr
`dacab4875df72109b96cc8977547a0dc04875bcd`. CI builds in the immutable
`zmkfirmware/zmk-build-arm` image recorded in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

From an environment with `west`, CMake, Python, and the Zephyr SDK installed:

```powershell
Set-Location firmware/zmk
west init -l config
west update --fetch-opt=--filter=tree:0
west zephyr-export
west spdx --init --build-dir=build/astrolabe
west build -s zmk/app -d build/astrolabe -b nice_nano_v2 -- `
  -DSHIELD=astrolabe `
  -DZMK_CONFIG="$PWD/config" `
  -DZMK_EXTRA_MODULES="$PWD/module"
west spdx --build-dir=build/astrolabe --analyze-includes --include-sdk
```

The CI artifact includes `zmk.uf2`, `zmk.elf`, the link map, compile commands, `.config`,
`zephyr.dts`, the source and frozen west manifests, checked-out project revisions, build identity,
Zephyr SPDX inventories, the human/machine-readable license bundle, and relative SHA-256 records.
The Arduino XIAO protocol-bench job remains alongside it until live replacement criteria are earned.

## Remaining release gates

[`../TODO.md`](../TODO.md) owns the P4 gate list; it is not restated here, because two copies
drift and the one that gets updated is the one the release process reads. That list covers final
assembly and electrical confirmation, the on-hardware firmware and daemon protocol matrices,
measurement on final hardware, the ZMK Studio decision, and the production USB VID/PID.

Only after those gates pass may the Arduino placeholder be deleted, the ZMK job replace the
protocol-bench release gate, or this record move under `archive/initiatives/`.
