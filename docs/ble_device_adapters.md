# BLE device descriptors and input snapshots

Astrolabe keeps Bluetooth transport, packet meaning, and binding semantics separate. The binding
engine sees only normalized `InputEvent` and `MotionSample` values; it does not know GATT UUIDs or
bit positions.

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
  "metadata": {"simultaneous_controls": "hardware_defined", "protocol_version": 1}
}
```

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

## Built-in mappings

- `astrolabe_5way` / `ble.astrolabe`: bit 0 Up, bit 1 Down, bit 2 Left, bit 3 Right,
  bit 4 Center; advertised name `Astrolabe`.
- `xiao3389_3button` / `ble.xiao3389`: bit 0 Left, bit 1 Right, bit 2 Middle; advertised name
  `Trackball BLE`. This is the working dual-PMW3389 test bench, not the production five-way board.

Descriptor metadata describes known capability without inventing switch mechanics. Final debounce
and physically possible simultaneous combinations must be measured on each hardware design.

