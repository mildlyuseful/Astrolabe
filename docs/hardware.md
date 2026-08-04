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
| Bootloader | Adafruit nRF52 UF2 with SoftDevice S140 7.3.0, application at `0x27000` |
| Motion sensors | Two PMW3610, sharing one bit-banged three-wire bus |
| Directional switch | ALPS SKRHADE010 five-way (four directions plus center push) |
| Firmware stack | Pinned ZMK and Zephyr commits plus one out-of-tree Astrolabe module; no ZMK patch |

The SuperMini is treated as nice!nano v2-compatible. That compatibility is a pin-and-peripheral
claim about the board Astrolabe actually ships, not a statement that the two boards are
interchangeable in general; it is what allows the upstream ZMK target to be used unforked.

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
| Sensor azimuth φ | 145°, 215° |
| Sensor polar angle θ | 120°, 120° |
| Sensor mount rotation | 270°, 270° |
| Sensor axis flip | both flipped |
| Housing frame tilt | −20° |

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

Only the bootloader gesture is observable without a host, by mounting its UF2 drive. The other three
change radio or endpoint state on a device with no indicator, so "nothing happened" and "it worked"
are indistinguishable — the open indicator decision is tracked in [`../TODO.md`](../TODO.md).

Reset is deliberately unbound: the hardware power switch covers it. Bond clear has no hardware
equivalent, which is why it gets the gesture instead — ZMK accepts a pairing only onto an open
slot, so without a bond clear all five profile slots eventually become unusable.

## What is not frozen

- Enclosure, mechanical mounting, and the physical sensor fixture.
- Battery cell selection and the charge path.
- USB VID/PID. Builds currently use upstream ZMK development identifiers, which must not be
  represented as release identity; a production allocation is a release gate.
- The hardware design source bundle required by CERN-OHL-W-2.0. Schematics, layout, and mechanical
  CAD do not yet exist in preferred form in this repository.
