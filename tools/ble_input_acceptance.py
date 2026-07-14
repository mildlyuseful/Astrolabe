"""Live BLE motion/input acceptance against the production transport and adapters."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trackball_daemon.devices import (  # noqa: E402
    BleTransport,
    DeviceAdapterRegistry,
    SnapshotInputProvider,
    builtin_device_descriptors,
)
from trackball_daemon.input import InputAggregator  # noqa: E402


ROTATION_UUID = "2cad0002-6e64-0146-b139-9cf2a4cd57fc"


def _event_rows(transitions):
    return [{
        "reason": transition.reason,
        "pressed": list(transition.snapshot.pressed_tokens),
        "events": [{
            "source": event.source_id,
            "control": event.control_id,
            "phase": event.phase.value,
            "sequence": event.sequence,
            "synthetic": bool(event.metadata.get("synthetic", False)),
        } for event in transition.events],
    } for transition in transitions if transition.events]


async def run(args):
    aggregator = InputAggregator()
    transitions = []
    aggregator.add_listener(transitions.append)
    descriptors = builtin_device_descriptors()
    providers = {}
    for descriptor in descriptors:
        provider = SnapshotInputProvider(
            descriptor, aggregator.accept_many, aggregator.update_health)
        aggregator.register_provider(provider)
        provider.configure(control.control_id for control in descriptor.controls)
        providers[descriptor.source_id] = provider
    registry = DeviceAdapterRegistry(descriptors, providers)
    stop = threading.Event()
    statuses = []
    motion_count = 0

    def on_status(message):
        statuses.append(message)
        print(message, flush=True)

    def on_motion(_sample):
        nonlocal motion_count
        motion_count += 1

    transport = BleTransport(
        lambda: (args.name, args.address, args.rotation_uuid),
        registry, on_motion, on_status, stop)
    task = asyncio.create_task(transport.run())
    try:
        await asyncio.sleep(max(1.0, args.seconds))
    finally:
        stop.set()
        await task
        aggregator.shutdown("acceptance_complete")

    report = {
        "name": args.name,
        "address": args.address,
        "motion_notifications": motion_count,
        "final_pressed": list(aggregator.snapshot().pressed_tokens),
        "final_health": {
            source: health.status.value
            for source, health in aggregator.snapshot().provider_health.items()
            if source.startswith("ble.")
        },
        "statuses": statuses,
        "transitions": _event_rows(transitions),
    }
    print(json.dumps(report, indent=2))
    if report["final_pressed"]:
        raise SystemExit("BLE provider left controls pressed after shutdown")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Trackball BLE")
    parser.add_argument("--address", default="")
    parser.add_argument("--rotation-uuid", default=ROTATION_UUID)
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
