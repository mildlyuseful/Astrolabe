# Frozen hardware contract

This is the product hardware contract for V1. The controller, sensor count and model, and five-way
switch below are frozen: they are no longer open design questions, and a change to any of them is a
product decision that invalidates the qualification evidence recorded against it, not a
configuration edit.

What is frozen is the *contract*. The values are still verified against a physical assembly by the
live gates in [`../TODO.md`](../TODO.md); freezing them fixes what those gates are testing, it does
not assert they have passed.

Firmware remains the authority for the values themselves. Every number here is transcribed from
[`../firmware/zmk/module/boards/shields/astrolabe/astrolabe.overlay`](../firmware/zmk/module/boards/shields/astrolabe/astrolabe.overlay)
and its keymap; when they disagree, the shield data is correct and this document is stale.

## Frozen selections

| Concern | Selection |
|---|---|
| Controller | SuperMini nRF52840 |
| ZMK board target | Upstream `nice_nano_v2`; no in-tree board fork |
| Bootloader | Adafruit nRF52 UF2 bundling SoftDevice S140 7.3.0; application at `0x27000`, `0xC5000` long |
| Motion sensors | Two PMW3610, sharing one bit-banged three-wire bus |
| Directional switch | ALPS SKRHADE010 five-way (four directions plus center push) |
| Status indicator | The controller's red user LED, P0.15 active-high (`blue_led` in the board definition — the label is the nice!nano's colour, not this board's) |
| Firmware stack | Pinned ZMK and Zephyr commits plus one out-of-tree Astrolabe module; no ZMK patch |

## The SuperMini is not flash-compatible with nice!nano v2

The upstream `nice_nano_v2` target is used because the SuperMini matches it in **pinout and
peripherals**. It does **not** match it in flash layout, and that difference is destructive:

| Board | SoftDevice | Occupies through | Application starts at |
|---|---|---|---|
| nice!nano v2 | S140 6.1.1 | `0x26000` | `0x26000` |
| SuperMini (ours) | S140 7.3.0 | `0x27000` | `0x27000` |

S140 7.x is one 4 KB page larger. A UF2 built with upstream's unmodified `0x26000` partition lands
*inside* the SoftDevice, so the bootloader refuses to start it and the board drops straight back to
bootloader mode without ever enumerating. There is no partial symptom — it either boots or it is
invisible.

The shield therefore overrides `code_partition` to `0x27000`/`0xC5000` in
[`astrolabe.overlay`](../firmware/zmk/module/boards/shields/astrolabe/astrolabe.overlay). This was
previously an opt-in overlay passed with `-DEXTRA_DTC_OVERLAY_FILE`, and CI, the documented build
command, and a hand build each independently forgot it, because nothing fails until the image
reaches hardware. It now travels with the shield and applies by construction. Do not make it
conditional; a genuine nice!nano v2 would need its own target.

Verify the assumption on any new board before flashing: `INFO_UF2.TXT` on the bootloader drive
reports the SoftDevice version.

## Pin map

Ball sensors, on one shared half-duplex bus:

| Signal | Pin |
|---|---|
| SCLK | P0.22 |
| SDIO | P0.24 |
| CS, sensor 0 | P1.00 |
| CS, sensor 1 | P0.20 |
| MOTION, sensor 0 | P0.11 |
| MOTION, sensor 1 | P0.17 |

Five-way switch, active-low with internal pull-ups:

| Position | Pin | Protocol bit | Standalone HID |
|---|---|---:|---|
| Up | P0.29 | 0 | none — hosts gestures |
| Down | P1.13 | 1 | left button |
| Left | P0.02 | 2 | none — hosts gestures |
| Right | P0.31 | 3 | right button |
| Center | P1.15 | 4 | middle button |

Center doubles as the forced-standalone escape: held through boot, it rejects daemon claims until
reboot. Both the route probe and the keyswitch scanner configure that pin, which is safe only
because the one-shot probe runs at `POST_KERNEL/40` and the scanner at `POST_KERNEL/90`, so the
scanner configures it last and owns the interrupt. Preserve that ordering if either initialization
priority moves.

Debounce is 8 ms on both press and release, owned by firmware.

The switch mechanism ordinarily permits only one direction at a time. That is descriptive metadata,
not a decoder restriction — the protocol and the daemon's provider accept any combination of
declared bits, so an unusually fast transition, a fault, or future hardware cannot strand a held
control.

## Ball geometry and sensor pose

| Parameter | Value |
|---|---|
| Ball diameter | 52000 µm (52 mm) |
| Sensor CPI | 1600 |
| Sensor azimuth φ | 140°, 220° |
| Sensor polar angle θ | 120°, 120° |
| Sensor mount rotation | 270°, 270° |
| Sensor axis flip | both flipped |
| Housing frame tilt | −25° |

Azimuth and frame tilt are the final fixture's measurements, not the prototype's (which were 145°/215°
and −20°). Polar angle, mount rotation, and flip carried over unchanged.

Ball diameter and CPI together fix the counts-per-radian conversion
(`radius_counts = (diameter_µm / 2000) × (CPI / 25.4)`, here ≈ 1638) that turns sensor counts into
the radians every downstream consumer works in. Expressing gains and gesture thresholds in radians
rather than counts is what lets the ball or the sensor change without silently retuning feel: a
larger ball sweeps more surface for the same angle, and the conversion absorbs exactly that.

## Power and radio

ZMK owns system idle and deep sleep, bonding, host profiles, endpoint selection, UF2 recovery, VDDH
battery sensing, and the standard BLE Battery Service.

The PMW3610 pair stays in its automatic Run/Rest policy (`FMODE=0`). A MOTION or button interrupt
opens the active polling window; when the window closes the MCU waits for the next interrupt. Device
suspend cancels polling and resume reopens the window without resetting the sensors or forcing a
performance mode.

SQUAL and shutter are parsed as diagnostics only. No validity filter, magnitude cap, or wake-settle
discard is applied in the shipped path; adding one requires the physical sleep/wake, surface-loss,
and illumination evidence called for in [`../TODO.md`](../TODO.md).

Battery presentation while charging or USB-powered is a physical gate. The daemon labels an active
USB session as externally powered rather than publishing a fresh USB-derived percentage.

## Standalone gestures

Only in standalone — the daemon layer is plain control-bit passthrough with no arbitration in front
of it. Up and Left carry no mouse button, which is what makes them available.

| Gesture | Effect |
|---|---|
| Double-tap Up | Toggle USB/BLE output |
| Double-tap Left | Next BLE profile |
| Hold Up 3 s | Enter bootloader |
| Hold Left 3 s | Clear the active BLE profile's bond |

Only the bootloader gesture confirms itself without a host, by mounting its UF2 drive. The other
three change radio or endpoint state, so they depend on the status indicator below to be observable
at all; until it is implemented, "nothing happened" and "it worked" are indistinguishable.

Reset is deliberately unbound: the hardware power switch covers it. Bond clear has no hardware
equivalent, which is why it gets the gesture instead — ZMK accepts a pairing only onto an open
slot, so without a bond clear all five profile slots eventually become unusable.

## Status indicator

The board carries two LEDs and only one of them is ours:

- **Red — the status indicator.** The user LED, driven by firmware. It is the LED the Arduino
  prototype drove through `LED_BUILTIN` and then disabled, and the one the UF2 bootloader flashes on
  reset and during bootload. This is the device's only output that does not require a host, so it
  carries BLE connection state, the active profile, and the selected endpoint — which is what makes
  the three non-bootloader gestures observable.
- **Blue — not ours.** A battery-charge indicator, lit by the charging circuit rather than by
  firmware. Do not bind it.

**The pin is P0.15, confirmed on hardware.** The upstream `nice_nano_v2` definition declares exactly
one GPIO LED, `blue_led` at P0.15 active-high, named for the colour it is on a real nice!nano. On
this board that same pin drives the *red* LED, verified by blinking it and observing which LED
responded. The `blue_led` label is inherited and misleading here; the node is correct, the name is
not. The shield aliases it to `astrolabe-status-led` so nothing downstream has to repeat that.

### Blink vocabulary

Event-driven: dark at rest, blinking only when something changes. A repeating state display would
cost battery during the time nobody is watching it, on a device whose discharge curve is not
measured yet.

| Change | Pattern |
|---|---|
| Endpoint selected | One long pulse, then **1 short** = USB, **2 short** = BLE |
| BLE profile or bond state | **N short** = profile N (one-based), then **one long** if that profile has no bond |

A leading long means endpoint; a leading short means profile. That grammar is what keeps them
readable apart, and it exists because ZMK raises the same `zmk_ble_active_profile_changed` event
for a profile switch, a bond clear, and a connection — carrying only an index. Patterns named after
actions would be ambiguous, so the vocabulary names the state the device ended up in instead.

One gap worth knowing before diagnosing a dead-looking device: clearing a bond on an
already-unbonded profile emits nothing at all, because ZMK's `clear_profile_bond()` returns early
when the peer is already unset and raises no event.

**The LED is safe to drive, and a prior comment claiming otherwise is wrong.** It was disabled during
normal operation in the Arduino prototype on the theory that its light reached both sensors off the
ball and corrupted their auto-exposure — see the note still standing at
[`../firmware/PMW3610/PMW3610.ino`](../firmware/PMW3610/PMW3610.ino). The phantom motion that
diagnosis was built on had a different cause: a rest-to-run wake transient, where the sensor's servo
re-locks and reports a decaying lockstep artifact. Fixing that at the source removed the symptom with
the LED still lit. Do not re-disable the LED on interference grounds without new evidence that
distinguishes it from the wake transient.

Implemented in
[`indicator.c`](../firmware/zmk/module/src/indicator.c) behind `CONFIG_ASTROLABE_INDICATOR`;
confirming each pattern on hardware is a gate in [`../TODO.md`](../TODO.md).

## What is not frozen

- Enclosure, mechanical mounting, and the physical sensor fixture.
- Battery cell selection and the charge path.
- USB PID. Builds currently enumerate as ZMK's own identifiers, `0x1D50:0x615E` — an OpenMoko
  sub-allocation belonging to the ZMK project, so shipping them would present Astrolabe as a generic
  ZMK device. The decision is to allocate under pid.codes (VID `0x1209`); the specific PID is not yet
  requested. Firmware descriptor and
  `trackball_daemon/devices/descriptor_data/astrolabe_5way.json` must change together.
- The hardware design source bundle required by CERN-OHL-W-2.0. Schematics, layout, and mechanical
  CAD do not yet exist in preferred form in this repository.
