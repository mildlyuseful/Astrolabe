# Astrolabe firmware

A [ZMK](https://zmk.dev) module that turns a microcontroller and something that measures rotation
into a 3D controller. On its own the device is an ordinary Bluetooth/USB mouse; when the Astrolabe
daemon claims it, the same device streams raw three-axis rotation and button state for 3D
navigation, and falls back to being a mouse the moment the daemon goes away.

The reference build is a trackball: a SuperMini nRF52840, two PMW3610 optical sensors on a 52 mm
ball, and a five-way switch ([`../docs/hardware.md`](../docs/hardware.md)).

## Build and flash

```sh
python firmware/build.py --flash
```

That is the whole loop. On first run it sets up a workspace in `~/.cache/astrolabe` (west, the
pinned ZMK/Zephyr checkouts, CMake, Ninja, flashing tools); the only prerequisite is the
[Zephyr SDK 0.16](https://github.com/zephyrproject-rtos/sdk-ng/releases/tag/v0.16.8) (the minimal
bundle plus the `arm-zephyr-eabi` toolchain) in `~/zephyr-sdk-0.16.8`.

Flashing needs no buttons. Running Astrolabe firmware reboots itself into the UF2 bootloader on
request; other TinyUSB/Arduino firmware is rebooted into serial DFU with the standard 1200-baud
touch. A board with nothing running can be put in its bootloader by double-tapping reset.

The image has to be linked for the SoftDevice the board's bootloader shipped with — 0x27000 after
S140 7.x, 0x26000 after S140 6.x — and SuperMini clones differ. `build.py` reads the version from
the bootloader and builds the matching layout; over serial DFU, where the bootloader cannot report
it, the package declares the SoftDevices it was linked for and the bootloader refuses a mismatch
before writing anything. `--layout s140-v6|s140-v7` forces one.

`--logging` builds a diagnostic image with a USB serial console and raw sensor output for
[`../tools/calibrate_sensor_mounts.py`](../tools/calibrate_sensor_mounts.py). Don't ship it.
The console holds its log for five seconds after boot so the boot messages survive enumeration;
open the board's COM port in any serial terminal within that window. The first line to look for
is `PMW3610 sensors ready: 0=1 1=1`. A sensor that does not identify logs the ID bytes it read:
`0x00/0xff` means nothing answered on SDIO at all, which is a wiring fault on SCLK, SDIO or that
sensor's CS, not a firmware or tuning one.

## Layout

```
config/                     the ZMK user config: pinned west manifest, keymap, Kconfig
module/                     the Astrolabe ZMK module
  include/astrolabe/
    astrolabe.h             the API a motion source and a keymap use
    protocol.h              the wire contract (see ../docs/protocol.md)
  src/
    route.c                 who owns input now, and paced delivery to them
    mouse.c                 the standalone HID mouse
    ble.c, usb.c            the daemon transports
    behaviors.c             &astro (a control bit) and &astro_out (output toggle)
    indicator.c             the status LED
  drivers/dual_pmw3610/     the reference motion source and its fusion math
  boards/shields/astrolabe/ pins, sensor geometry, mouse tuning
  dts/bindings/
build.py
```

## How it fits together

A **motion source** measures rotation and reports it in radians. A **control** is a button the
keymap reports by protocol bit. The **route** decides where both go:

- **Standalone** (the resting state): `mouse.c` turns tilt into cursor motion and twist into scroll,
  classifying each 7 ms window as a whole, and controls into clicks per the shield's
  `control-buttons`.
- **Daemon**: a client that subscribes over BLE, or attaches over the USB vendor interface, claims
  the route and receives rotation and full control snapshots instead. It must keep sending
  keepalives; when it stops, disconnects, or detaches, the route returns to standalone on its own,
  releasing any click it had taken over. USB wins over BLE.

The route also owns keymap layer 1, so standalone-only gestures cannot fire while a daemon owns
input and daemon controls carry no tap-dance delay.

## Make your own controller

Anything that can measure rotation can drive this. Write a Zephyr driver that calls:

```c
#include <astrolabe/astrolabe.h>

astrolabe_report_motion(rx, ry, rz);   /* radians: +X right, +Y away, +Z up, right-handed */
astrolabe_register_source(wake);       /* optional: called on every control press/release */
```

`report_motion` can be called at any rate from any thread; the core accumulates and paces delivery.
Then give your shield an `astrolabe,controller` node (copy the reference one: mouse gains are per
radian, so they carry over), bind controls with `&astro BIT` in the keymap, and the daemon, the
transports, and the standalone mouse all come with it. A device the daemon should recognize needs
a descriptor in `trackball_daemon/devices/descriptor_data/` naming its controls.

## Standalone gestures and the status LED

| Gesture | Effect |
|---|---|
| Double-tap Up | Toggle USB/BLE output |
| Double-tap Left | Next BLE profile |
| Hold Up 3 s | UF2 bootloader |
| Hold Left 3 s | Clear the active profile's bond |
| Hold Center through power-on | Refuse daemon claims until reboot |

The LED is dark at rest and blinks only on a change: a leading long blink means endpoint (then 1
short = USB, 2 = BLE), a leading short means BLE profile N, and a trailing long means what you
selected is not usable (no bond, or the toggle could not apply).
