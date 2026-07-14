# Phase 6 BLE and firmware baseline

This record freezes the compatibility boundary at Phase 6 start commit `af373fa`. It distinguishes
behavior proven by source/tests from behavior that still requires physical hardware.

## Existing GATT and motion contract

- Custom service: `2cad0001-6e64-0146-b139-9cf2a4cd57fc`.
- Rotation characteristic: `2cad0002-6e64-0146-b139-9cf2a4cd57fc`.
- Rotation value: exactly 12 bytes containing three little-endian IEEE-754 `float32` deltas
  `(rx, ry, rz)` in radians. The characteristic and byte shape are compatibility-frozen.
- `trackball_daemon.ble` currently scans by the configured name or connects by address, subscribes
  to exactly the configured rotation characteristic, and forwards uninterpreted bytes to `App`.
  It has no characteristic discovery, adapter selection, input-state decoder, or disconnect event
  for normalized controls.
- The Phase 6 untouched Python baseline is 532 passing tests. There were no focused BLE transport or
  packet-vector tests.

## Working firmware at the phase boundary

`firmware/XIAO3389/XIAO3389.ino` is the only working sketch. It is dual-PMW3389 test-bench firmware,
not the future production-hardware sketch. Its controls are three independent active-low buttons:

| Control | Pin | Existing HID mask |
|---|---|---:|
| Left | `D0` | `0x01` |
| Right | `D1` | `0x02` |
| Middle | `D2` | `0x04` |

Each input is debounced independently: a raw transition must remain unchanged for `DEBOUNCE_MS =
8` before the stable state changes. The firmware samples buttons in the 1 kHz sensor loop.

Enabling notifications on the rotation characteristic owns controller mode. Controller mode:

- suppresses HID pointer, wheel, and button reports;
- clears pointer/wheel accumulators;
- releases an HID button report left held when the mode changed; and
- continues publishing the unchanged rotation characteristic.

Disabling rotation notifications or disconnecting the owning link returns to HID mode. With no
daemon subscription, ordinary BLE HID motion, scrolling, and three buttons remain available.

`firmware/Astrolabe/Astrolabe.ino` is an empty production placeholder. It contains no sensor, BLE,
HID, five-way pin, or debounce implementation. No repository source identifies the physical pins
for Up/Down/Left/Right/Center, so Phase 6 must not invent that hardware mapping. The working
three-button sketch is the available protocol test bench; final five-way firmware and live
five-way acceptance require the production board mapping.

After this baseline was frozen, the production switch's electrical and mechanical constraints were
confirmed: its buttons are active-low with internal pull-ups, debounce is performed in firmware,
and the mechanism ordinarily permits only one direction at a time. Simultaneous declared bits
remain valid on the wire and in the host decoder as a contingency for fast/forceful transitions,
faults, and future devices. The final pins and tuned debounce interval remain intentionally
unspecified until the hardware design is complete.

## Additive Phase 6 protocol allocation

The following additive values are reserved without changing the existing service or rotation
characteristic:

- Input-state characteristic: `2cad0003-6e64-0146-b139-9cf2a4cd57fc`.
- Protocol version: `1`.
- Message kind `1`: full input-state snapshot.
- Header: version byte, kind byte, unsigned 16-bit little-endian sequence, payload length byte.
- Payload: `N` bytes of pressed-control bits, least-significant bit first according to the selected
  data descriptor.

Sequence comparison uses `delta = (incoming - previous) & 0xffff`: zero is duplicate,
`1..0x7fff` is newer (including wrap), and `0x8000..0xffff` is stale. Every new subscription accepts
its first valid snapshot unconditionally. Invalid version, kind, length, or out-of-range set bits
must not mutate pressed state.

Built-in descriptor intent:

- `astrolabe_5way`: bits 0..4 are Up, Down, Left, Right, Center.
- `xiao3389_3button`: bits 0..2 are Left, Right, Middle for the working test bench.

The descriptor, not the binding engine or packet decoder, owns bit-to-control meaning. Third-party
hardware remains data-only at this boundary; executable adapter plug-ins are not trusted or loaded.

## Adjacent packaging debt

`pyproject.toml` explicitly packages only `trackball_daemon`, even though Phase 5 added the
`trackball_daemon.input` subpackage and Phase 6 adds `trackball_daemon.devices`. Phase 6 must switch
to bounded `trackball_daemon*` package discovery and assert that both subpackages and descriptor
JSON data are present in built metadata.
