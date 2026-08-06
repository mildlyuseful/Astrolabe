# BLE/USB device descriptors and input snapshots

Astrolabe keeps transport, packet meaning, and binding semantics separate. The binding
engine sees only normalized `InputEvent` and `MotionSample` values; it does not know GATT UUIDs or
HID report IDs. Shared provider, state, and lifecycle ownership is defined in
[`architecture.md`](architecture.md).

## Frozen payload protocol

The existing service and rotation characteristic are compatibility-frozen:

- Service: `2cad0001-6e64-0146-b139-9cf2a4cd57fc`
- Rotation: `2cad0002-6e64-0146-b139-9cf2a4cd57fc`
- Rotation value: exactly 12 bytes, little-endian `float32 rx, ry, rz`

Input-capable firmware adds:

- Input state: `2cad0003-6e64-0146-b139-9cf2a4cd57fc`
- Keepalive: `2cad0004-6e64-0146-b139-9cf2a4cd57fc`

Reading the keepalive characteristic returns `[version=1, timeout_ms_le16]`. Writing anything to it,
as the route owner, refreshes the claim; a non-owner write is rejected, which is how a daemon learns
it has lost the route. Firmware releases the route when no write arrives inside the window, and the
daemon paces writes at a third of it.

That fail-safe is not optional bookkeeping. A CCC is persisted into the bond and the BLE link
outlives the daemon — Windows holds it open for the HID mouse — so before this existed, a daemon
that died without unsubscribing left the route claimed with no disconnect to notice. The claim was
then restored on every reconnect, so it survived a power cycle, and the device presented as
connected with a dead cursor and no working gestures until the bond was forgotten or the firmware
reflashed. Subscribing now means "a daemon is alive", not "a daemon once was".

Input packets are full snapshots, not edge messages:

| Offset | Size | Meaning |
|---:|---:|---|
| 0 | 1 | Protocol version (`1`) |
| 1 | 1 | Message kind (`1` = input-state snapshot) |
| 2 | 2 | Unsigned 16-bit sequence, little-endian |
| 4 | 1 | Payload byte count `N` |
| 5 | `N` | Pressed-control bitset, least-significant bit first |

`N` must be 1–32 and the packet must be exactly `5 + N` bytes. Set bits not declared by the selected
descriptor invalidate the whole packet. Invalid, duplicate, or stale packets do not change pressed
state.

For sequence `incoming` after `previous`, compute
`delta = (incoming - previous) & 0xffff`:

- `0`: duplicate;
- `1..0x7fff`: newer, including wrap from `0xffff` to `0`;
- `0x8000..0xffff`: stale/out of order.

A new connection/subscription session resets this comparison and accepts its first valid snapshot.
The next accepted full snapshot repairs any missed intermediate transition. Each session has an
opaque identity lease; a notification or disconnect captured by an older BLE or USB session cannot
change its replacement. Exact-session disconnect releases every control still owned by that device
namespace.

## Data descriptor format

Built-ins live in `trackball_daemon/devices/descriptor_data`. A descriptor contains data only:

```json
{
  "schema_version": 1,
  "device_id": "example_pad",
  "source_id": "ble.example",
  "label": "Example BLE pad",
  "match": {"advertised_names": ["Example Pad"]},
  "service_uuid": "2cad0001-6e64-0146-b139-9cf2a4cd57fc",
  "motion_characteristic": "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
  "input_characteristic": "2cad0003-6e64-0146-b139-9cf2a4cd57fc",
  "controls": [
    {"id": "button.primary", "label": "Primary", "bit": 0, "kind": "button"}
  ],
  "metadata": {"simultaneous_controls": "mechanically_exclusive_not_enforced", "protocol_version": 1}
}
```

`keepalive_characteristic` is optional and omitted above deliberately: absent means the device grants
route ownership for as long as the subscription lasts, which is the right model for firmware whose
link cannot outlive its host session. Declare it only for firmware that expires an idle claim, as
`astrolabe_5way` does.

A device that also exposes a wired route adds an exact vendor-HID identity under
`match.usb_hid`. The field is optional and does not change the schema version: it is additive, so
every descriptor written before it existed stays valid, and a BLE-only device simply omits it.

```json
{
  "schema_version": 1,
  "match": {
    "advertised_names": ["Astrolabe"],
    "usb_hid": {
      "vendor_id": 7504,
      "product_id": 24926,
      "usage_page": 65280,
      "usage": 1,
      "product": "Astrolabe"
    }
  }
}
```

The packaged schema is `trackball_daemon/schemas/device-descriptor-v1.schema.json`; a
parser-validated contributor example is
`trackball_daemon/examples/device-descriptor.example.json`. JSON Schema checks portable shape,
while `load_device_descriptor` remains authoritative for case-insensitive advertised-name
uniqueness, exact USB identity, UUID normalization, unique IDs/bits, and the
control/input-characteristic relationship. The schema version tracks the document format, not
device capability: do not bump it to signal that a device gained a transport.

Stable binding tokens are `source_id:control.id`, such as
`ble.astrolabe:fiveway.center`. IDs, labels, bits, UUIDs, and advertised names are validated. Unknown
root/control keys—including module or callback names—are rejected.

The registration boundary is deliberately small:

1. Load JSON with `load_device_descriptor` (or `builtin_device_descriptors`).
2. Construct one `SnapshotInputProvider` for its control namespace and register that provider with
   the daemon's `InputAggregator`.
3. Pass the descriptors and source-ID-to-provider map to `DeviceAdapterRegistry` and
   `UsbTransport`.
4. `BleTransport` selects an input-capable adapter only when name, service, rotation UUID, and input
   characteristic match. Otherwise it uses the rotation-only legacy adapter and never guesses a
   bit mapping.
5. `UsbTransport` opens only an interface matching VID, PID, usage page, usage, and product string,
   validates its feature capability, and completes an acknowledged attach before replacing the
   provider session.

Battery status remains outside this device-specific adapter selection. When the standard Battery
Level characteristic (`0x2A19`) exists, `BleTransport` validates its one-byte 0–100 value, reads it
at connection, and subscribes for changes. The daemon presents the same current or last-known value
in Settings and the tray. A connected device without that optional characteristic reports Battery
as unavailable.

This is an API for trusted application composition and open-source contributions, not automatic
execution of files found on disk. Third-party Python adapter loading is intentionally unsupported.
A device with a different wire protocol requires reviewed code; a device using this snapshot wire
format requires only reviewed descriptor data.

## Discovery identity

A configured BLE address is authoritative and bypasses name discovery. Without an address,
`BleTransport` compares the configured name case-insensitively with both the current advertisement's
local name and the operating system's cached device name. Some peripherals put the name in a
separate scan response, so either source may be the only one available during a scan.

A scan alone is not sufficient on Windows. Once the device is paired as a BLE HID mouse the OS holds
a connection to it and it stops advertising, so it becomes invisible to discovery while sitting
right there, connected — and the daemon reports "not found" for a device the user can see in
Bluetooth settings. The transport therefore remembers the address of the last device that selected
an adapter and retries it directly when a scan comes back empty, since bleak can reach a known
address without an advertisement. Setting the Device address explicitly skips the scan entirely and
is the reliable configuration for a device that is normally also paired for HID.

Seeing a descriptor-compatible service UUID without the configured name is diagnostic evidence, not
permission to connect. Built-in devices may share the same service and motion characteristic while
publishing different input layouts, so the transport reports the observed address/name and asks for
an explicit Device name or address instead of guessing a descriptor.

USB discovery is deliberately stricter and never falls back to name alone. All five fields in the
`usb_hid` match must agree with one enumerated HID collection. The current VID/PID are ZMK's own
`0x1D50:0x615E`, shared by every ZMK board; a pid.codes allocation under VID `0x1209` is pending and
must land in the firmware descriptor and this descriptor data together before hardware is
distributed.

## USB vendor-HID framing

The ZMK firmware exposes a second HID instance with vendor usage page `0xFF00`, usage `1`. Every
host report begins with its report ID; the sizes below are payload sizes after that byte.

| ID | Direction | Payload |
|---:|---|---|
| 1 | Input | frozen 12-byte rotation value |
| 2 | Input | frozen 6-byte v1 input snapshot |
| 3 | Output | `[version=1, opcode, request_id_le16]` |
| 4 | Feature | `[version=1, firmware_revision, report_mask, flags, keepalive_ms_le16, usb_revision=1, reserved=0]` |
| 5 | Input | `[version=1, opcode, request_id_le16, result, owner]` |

Opcodes are attach=1, detach=2, and keepalive=3. Results are success=0,
forced-standalone=1, and invalid=2. Owners are standalone=0, BLE=1, and USB=2. Attach is accepted
only when the matching ACK names USB as owner. The daemon then installs the USB provider lease
before pausing BLE. Keepalive runs within the firmware-advertised timeout; cable loss, endpoint
failure, timeout, or detach releases the exact USB owner and restores BLE eligibility. Firmware
also reconsiders a still-connected subscribed BLE client after USB release, covering an accepted
attach whose ACK never reached the daemon. USB has priority if a new wired lease wins that race.

The firmware checks the underlying USB status for suspend whenever ZMK publishes a connection-state
event. Because ZMK represents suspend and configured HID with the same public connection state, a
transient suspend may be coalesced; endpoint-write failure and the keepalive timeout are the safety
fallback. Immediate suspend release remains a live Windows verification question, not an automated
claim.

The feature and ACK reports are transport framing, not extensions to the frozen values carried by
reports 1 and 2. While USB is active the daemon reports external power explicitly and treats any
battery percentage as last-known; it does not infer state of charge from the USB-powered voltage.

## Built-in mappings

- `astrolabe_5way` / `ble.astrolabe`: bit 0 Up, bit 1 Down, bit 2 Left, bit 3 Right,
  bit 4 Center; advertised name `Astrolabe`.
- `xiao3389_3button` / `ble.xiao3389`: bit 0 Left, bit 1 Right, bit 2 Middle; advertised name
  `Trackball BLE`. This is the dual-PMW3389 three-button test bench, not the five-way board.

The `astrolabe_5way` publisher is the ZMK production firmware under
[`../firmware/zmk/`](../firmware/zmk/), on the hardware frozen in [`hardware.md`](hardware.md).
Standalone HID maps Down/Right/Center to left/right/middle mouse buttons; Up and Left carry no
button and host the recovery and radio gestures instead. The sensor path keeps Performance
`FMODE=0`, letting the sensors manage Run/Rest automatically, so the MCU can wait for MOTION while
motion bursts remain readable in automatic Rest. It uses neither forced Rest nor Force Awake for
normal power management. The board's red user LED reports BLE profile, bond state, and the selected
endpoint as event-driven blink patterns; the vocabulary is in [`hardware.md`](hardware.md).

Its source and toolchain build are automated and it has been exercised on the prototype fixture, but
the final assembly's electrical map, sensor geometry, switch mechanics, enclosure, sleep/wake
behavior, and live BLE/USB route matrices remain unqualified as listed in
[`../TODO.md`](../TODO.md).

ZMK owns the standard BLE Battery Service, sampling the nRF52840's internal `VDDH/5` ADC input.
SuperMini hardware drives VDDH from USB while attached, so a USB-powered reading is not battery
voltage; the daemon labels an active USB session as externally powered and treats any percentage as
last-known rather than inferring state of charge. Adding the service changed the GATT database, so a
host bonded before it existed may need the device removed and paired again before Battery Level
appears.

[`firmware/PMW3610/PMW3610.ino`](../firmware/PMW3610/PMW3610.ino) is the Arduino validation
prototype that established this behavior on the same controller — dual PMW3610, interrupt-driven
reads, the five-way bit map, and a once-per-minute 3.3–4.2 V battery estimate that held its last
battery-only value across USB attach. It is retained for diagnosis, not as a supported publisher,
and its `BALL_DIAMETER_MM` is still the prototype's 50.8, so its rotation output is uncalibrated
against the shipped 52 mm ball.

For repeatable PMW3610 transition characterization, set `PMW_WAKE_STRESS` to `1`, flash the prototype,
keep the ball stationary, and run:

```powershell
python tools/pmw3610_wake_stress.py --serial auto --cycles 100 --log wake.log
```

The diagnostic pauses normal HID/BLE processing and, on each serial command, forces Rest1, restores
normal operation, and records raw deltas, Motion bits, SQUAL, shutter, and observed sensor modes. It
exists to reproduce and classify the suspect transition; forced modes are not the production power
policy. Restore `PMW_WAKE_STRESS` to `0` and reflash before ordinary testing.

The ALPS SKRHADE010 five-way switch is active-low with internal pull-ups, with debounce owned by
firmware; its pin map and timing are in [`hardware.md`](hardware.md). Its
mechanism ordinarily permits only one direction at a time. That is descriptive hardware metadata,
not a decoder restriction: the protocol and provider accept any combination of declared bits so an
unusually fast/forceful transition, a fault, or future hardware cannot strand a hold.
