# ZMK hardware-freeze live evidence — 2026-08-06

Live results from the `docs/hardware-freeze` branch, run on the single existing assembly: SuperMini
nRF52840, two PMW3610 sensors, SKRHADE010 five-way, 52 mm ball. Artifact is the branch's ZMK build at
`7105812` unless a section names an earlier commit, flashed over USB as `zmk.uf2`.

These are bench observations, not instrumented captures: pass means the described exercise produced
the described behavior repeatedly by hand. No current, latency, or throughput figures were taken, so
nothing here closes a gate that asks for a measurement. Remaining work stays in
[`../../TODO.md`](../../TODO.md).

## Flash layout — application at 0x27000

- Exercise: flash a build whose `&code_partition` override was absent, then one carrying it.
- Result: pass for `0x27000`, fail for `0x26000`. The nice!nano v2 default lands inside the
  SuperMini's S140 7.3.0 SoftDevice and produces a board that never enumerates and never advertises;
  nothing fails at build time. `0x27000` boots.
- Baseline change: the override moved out of an opt-in `-DEXTRA_DTC_OVERLAY_FILE` file and into
  `firmware/zmk/module/boards/shields/astrolabe/astrolabe.overlay`, so it cannot be forgotten, and
  CI now reads the address back out of the UF2 rather than re-asserting the constant. The opt-in
  overlay had documented this exact hazard and was still bypassed three times in one session — by
  CI, by the documented build command, and by a hand build.

## Status indicator — pin and patterns

- Exercise: a pin-identification build blinking each candidate, then each pattern in the indicator
  vocabulary on the shipping build.
- Result: pass. P0.15 drives the red LED, which is the node the board names `blue_led`. The
  disabled-LED interference claim inherited from the `.ino` firmware does not reproduce: neither
  sensor is disturbed with the LED deliberately lit.
- Patterns confirmed: endpoint toggle shows long-then-one for USB and long-then-two for BLE; a
  profile switch shows the matching count; a bond clear adds the trailing long; a toggle that cannot
  be applied shows the trailing long rather than nothing; a bond clear on an already-unbonded profile
  shows nothing, which is ZMK raising no event rather than a fault.
- Baseline change: the indicator gates endpoint patterns on transport change, not on
  `zmk_endpoint_changed` firing, because the BLE endpoint instance embeds `profile_index` and a
  profile switch therefore raises the endpoint event too.

## BLE keepalive fail-safe

- Exercise: kill the daemon without a clean shutdown while Windows holds the HID link; separately,
  power the device off while connected. Then reconnect and repeat with the CCC restored from the
  bond. Ordinary sessions were run alongside to confirm none expired underneath itself.
- Result: pass. The route returns to standalone within the 2 s window in every case.
- Baseline change: this is the fix for the only reproducible brick found — a connected device with a
  dead cursor and no working gestures, which is silent from the host side. The route is now claimed
  by a keepalive write from a subscribed non-owner rather than by subscription alone, and
  `restore_candidate()` is gone.

## Daemon BLE reachability while Windows holds the HID link

- Artifact: daemon on bleak 3.0.2 (`>=3.0.2,<4`).
- Exercise: pair the device to Windows over BLE HID, then start the daemon and let it take the
  route. Includes the fresh-start case with no address in memory, long sessions under notify, and
  disconnecting while an input was held.
- Result: pass. A device the OS is already holding stops advertising, so the daemon must connect to
  a known address with no advertisement; that path works.
- Baseline change: two causes, both required. `BleakClient` does not resolve a bare address string
  up front — it defers a scan into `connect()` — so the daemon now hands it a `BLEDevice`. And the
  learned address is persisted to `device.address` instead of being held in memory, where it was
  empty on every fresh start and the fallback was therefore inert.

## Mouse buttons — two stuck-click defects

- Exercise: hold left and right click over BLE HID, over wired HID, and across a daemon route seize.
- Result: pass after the fix; both defects reproduced every time before it.
  - A held click stayed down for almost exactly the duration it was held, on both transports.
    `zmk_hid_mouse_button_press` is reference counted, not idempotent, so re-asserting a held button
    every output cycle incremented `explicit_button_counts` once per cycle and the release drained
    one count. The route now drives only the difference against `applied_buttons`.
  - On a daemon seize, the first held left click stuck down completely until the next click. The
    claim path revoked the previous transport without releasing what it had asserted.
- Rejected on the way: BLE connection-interval pacing, added for a HOG-queue-backlog theory. It
  changed nothing, and the defect reproducing identically on wired HID disproved the theory. Fully
  reverted rather than left in as harmless.

## Standalone/daemon layer split

- Exercise: cable pull, daemon termination, and keepalive timeout, plus a route change landing
  mid-gesture.
- Result: pass. The layer follows the route with no manual step, and a mid-gesture route change
  leaves neither a stuck layer nor a stranded held control.

## What this run did not establish

Stated so absence of a number is not later read as a passed gate:

- No battery, current-draw, deep-sleep, wake, connection-interval, throughput, latency, or
  flash/RAM measurement was taken. The PMW3610 Run/Rest and ZMK deep-sleep characterization is
  untouched.
- Sensor pose was exercised for feel, not calibrated. A pose error shows up as cross-axis bleed
  rather than an obviously wrong direction, and no axis-isolation pass was run against the 140°/220°
  azimuth and −25° frame tilt now in the shield.
- The five-way switch was exercised in use, not characterized: no bounce-profile measurement behind
  the 8 ms debounce, no diagonal false-center check.
- Builds still enumerate as `0x1D50:0x615E`, ZMK's own OpenMoko sub-allocation.
