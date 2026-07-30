# BLE device descriptors and input snapshots

Astrolabe keeps Bluetooth transport, packet meaning, and binding semantics separate. The binding
engine sees only normalized `InputEvent` and `MotionSample` values; it does not know GATT UUIDs or
bit positions. Shared provider, state, and lifecycle ownership is defined in
[`architecture.md`](architecture.md).

## Built-in protocol

The existing service and rotation characteristic are compatibility-frozen:

- Service: `2cad0001-6e64-0146-b139-9cf2a4cd57fc`
- Rotation: `2cad0002-6e64-0146-b139-9cf2a4cd57fc`
- Rotation value: exactly 12 bytes, little-endian `float32 rx, ry, rz`

Input-capable firmware adds:

- Input state: `2cad0003-6e64-0146-b139-9cf2a4cd57fc`

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
The next accepted full snapshot repairs any missed intermediate transition. Disconnect emits a
provider disconnect and releases every control still owned by that device namespace.

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

The packaged JSON Schema is
`trackball_daemon/schemas/device-descriptor-v1.schema.json`; a parser-validated contributor example
is `trackball_daemon/examples/device-descriptor.example.json`. JSON Schema checks portable shape,
while `load_device_descriptor` remains authoritative for case-insensitive advertised-name
uniqueness, UUID normalization, unique IDs/bits, and the control/input-characteristic relationship.

Stable binding tokens are `source_id:control.id`, such as
`ble.astrolabe:fiveway.center`. IDs, labels, bits, UUIDs, and advertised names are validated. Unknown
root/control keys—including module or callback names—are rejected.

The registration boundary is deliberately small:

1. Load JSON with `load_device_descriptor` (or `builtin_device_descriptors`).
2. Construct one `SnapshotInputProvider` for its control namespace and register that provider with
   the daemon's `InputAggregator`.
3. Pass the descriptors and source-ID-to-provider map to `DeviceAdapterRegistry`.
4. `BleTransport` selects an input-capable adapter only when name, service, rotation UUID, and input
   characteristic match. Otherwise it uses the rotation-only legacy adapter and never guesses a
   bit mapping.

This is an API for trusted application composition and open-source contributions, not automatic
execution of files found on disk. Third-party Python adapter loading is intentionally unsupported.
A device with a different wire protocol requires reviewed code; a device using this snapshot wire
format requires only reviewed descriptor data.

## Discovery identity

A configured BLE address is authoritative and bypasses name discovery. Without an address,
`BleTransport` compares the configured name case-insensitively with both the current advertisement's
local name and the operating system's cached device name. Some peripherals put the name in a
separate scan response, so either source may be the only one available during a scan.

Seeing a descriptor-compatible service UUID without the configured name is diagnostic evidence, not
permission to connect. Built-in devices may share the same service and motion characteristic while
publishing different input layouts, so the transport reports the observed address/name and asks for
an explicit Device name or address instead of guessing a descriptor.

## Built-in mappings

- `astrolabe_5way` / `ble.astrolabe`: bit 0 Up, bit 1 Down, bit 2 Left, bit 3 Right,
  bit 4 Center; advertised name `Astrolabe`.
- `xiao3389_3button` / `ble.xiao3389`: bit 0 Left, bit 1 Right, bit 2 Middle; advertised name
  `Trackball BLE`. This is the dual-PMW3389 three-button test bench, not the five-way board.

The validated five-way prototype publisher is
[`firmware/PMW3610/PMW3610.ino`](../firmware/PMW3610/PMW3610.ino): SuperMini nRF52840, dual
PMW3610 sensors, interrupt-driven reads, and the five-way protocol bit map under the `Astrolabe`
advertised name. Standalone HID maps Down/Right/Center to left/right/middle mouse buttons; Up and Left
are protocol-only. Motion reported while the sensors re-lock after rest is drained through a bounded
minimum-plus-quiet wake guard before either output path can consume it. Its completed validation does
not qualify the final Seeed Studio XIAO nRF52840 product hardware. Prototype pins, 2.0 in ball
diameter, mount angles, and wake timings live only in that sketch.

The five-way switch is active-low with internal pull-ups, with debounce owned by firmware. Its
mechanism ordinarily permits only one direction at a time. That is descriptive hardware metadata,
not a decoder restriction: the protocol and provider accept any combination of declared bits so an
unusually fast/forceful transition, a fault, or future hardware cannot strand a hold.
