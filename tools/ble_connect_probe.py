# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Report why the daemon cannot reach the trackball over BLE.

The daemon's own status line collapses every failure into "connection error, retrying", which is
not enough to tell a discovery problem from a GATT one. This walks the same path in stages and says
which stage failed, so the next fix is chosen from evidence instead of inference.

Find the address in Windows Settings -> Bluetooth & devices -> the device -> Properties, or in
Device Manager -> the device -> Details -> "Bluetooth address".

    python tools/ble_connect_probe.py --address AA:BB:CC:DD:EE:FF

Run it with the device in the state that fails: paired as a BLE HID mouse, daemon closed.
"""

import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice


SERVICE = "2cad0001-6e64-0146-b139-9cf2a4cd57fc"
ROTATION = "2cad0002-6e64-0146-b139-9cf2a4cd57fc"
INPUT = "2cad0003-6e64-0146-b139-9cf2a4cd57fc"
KEEPALIVE = "2cad0004-6e64-0146-b139-9cf2a4cd57fc"

EXPECTED = {SERVICE: "service", ROTATION: "rotation", INPUT: "input", KEEPALIVE: "keepalive"}


def _head(text):
    print(f"\n=== {text} ===")


async def _scan(name):
    _head("stage 1: scan")
    print("A device Windows already holds as a HID mouse does not advertise, so 'not seen' here is")
    print("expected and is not itself the bug.")
    try:
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: (getattr(adv, "local_name", None) or getattr(d, "name", "") or "")
            .casefold() == name.casefold(),
            timeout=8.0,
        )
    except Exception as exc:
        print(f"  scan raised {type(exc).__name__}: {exc}")
        return
    print(f"  {'seen advertising' if device else 'not seen (expected while paired for HID)'}")


async def _connect(address, name, cached):
    label = "cached services" if cached else "uncached services"
    _head(f"stage 2: connect by address, {label}")
    target = BLEDevice(address, name, None)
    kwargs = {} if cached else {"winrt": {"use_cached_services": False}}
    try:
        async with BleakClient(target, timeout=20.0, **kwargs) as client:
            print(f"  connected={client.is_connected}")
            found = {}
            for service in client.services:
                for characteristic in service.characteristics:
                    if characteristic.uuid.lower() in EXPECTED:
                        found[characteristic.uuid.lower()] = characteristic.properties
                if service.uuid.lower() == SERVICE:
                    found[SERVICE] = "present"
            _head(f"stage 3: Astrolabe GATT, {label}")
            for uuid, role in EXPECTED.items():
                state = found.get(uuid)
                print(f"  {role:<10} {uuid}  {'MISSING' if state is None else state}")
            if SERVICE not in found:
                print("  -> service absent: Windows served a GATT database without it.")
                if cached:
                    print("     Retrying uncached will show whether the cache is stale.")
            return SERVICE in found
    except Exception as exc:
        print(f"  FAILED {type(exc).__name__}: {exc}")
        return None


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="e.g. AA:BB:CC:DD:EE:FF")
    parser.add_argument("--name", default="Astrolabe")
    args = parser.parse_args()

    await _scan(args.name)
    ok = await _connect(args.address, args.name, cached=True)
    if ok is not True:
        await _connect(args.address, args.name, cached=False)

    _head("what this tells us")
    print("stage 2 fails            -> the OS refuses a second GATT client; needs a different")
    print("                            approach than address resolution.")
    print("stage 2 ok, 3 incomplete -> stale cached GATT database; the daemon needs")
    print("                            use_cached_services=False.")
    print("both stages ok           -> the transport reaches the device and the failure is above")
    print("                            this layer, in adapter selection or subscription.")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("This probe is about Windows BLE behavior.", file=sys.stderr)
    asyncio.run(main())
