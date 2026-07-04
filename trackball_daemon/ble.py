"""BLE / data-ingestion layer -- UNCHANGED behavior.

This is the original cube_test.py ble_loop, byte-for-byte in control flow (scan ->
connect -> subscribe -> hold -> reconnect, same timeouts/sleeps). The only difference is
that the device name/address/characteristic are read from get_params() at the top of each
iteration (instead of module constants), so a config edit takes effect on the next
reconnect. The notification callback and the protocol are untouched.
"""
import asyncio
import threading

from bleak import BleakScanner, BleakClient


async def ble_loop(get_params, notify_cb, status_cb, stop_event):
    while not stop_event.is_set():
        name, address, char_uuid = get_params()
        if address:
            target = address
            status_cb(f"connecting to {address}...")
        else:
            status_cb(f'scanning for "{name}"...')
            try:
                device = await BleakScanner.find_device_by_name(name, timeout=10.0)
            except Exception as exc:                   # adapter hiccup, etc.
                status_cb(f"scan error: {exc}")
                await asyncio.sleep(2.0)
                continue
            if device is None:
                status_cb("device not found, retrying...")
                await asyncio.sleep(1.0)
                continue
            target = device

        try:
            async with BleakClient(target) as client:
                status_cb(f"connected to {client.address}")
                await client.start_notify(char_uuid, notify_cb)
                status_cb("subscribed -- ball is live")
                while client.is_connected and not stop_event.is_set():
                    await asyncio.sleep(0.3)
                try:
                    await client.stop_notify(char_uuid)
                except Exception:
                    pass
            status_cb("disconnected, reconnecting...")
        except Exception as exc:
            status_cb(f"connection error: {exc}, retrying...")
            await asyncio.sleep(2.0)


def start_ble_thread(get_params, notify_cb, status_cb, stop_event):
    def runner():
        try:
            asyncio.run(ble_loop(get_params, notify_cb, status_cb, stop_event))
        except Exception as exc:
            status_cb(f"BLE thread stopped: {exc}")
    t = threading.Thread(target=runner, name="ble", daemon=True)
    t.start()
    return t
