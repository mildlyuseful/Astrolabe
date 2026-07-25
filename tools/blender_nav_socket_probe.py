# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""End-to-end SOCKET probe: run the real NavBroker and the add-on in one headless Blender process
and prove a frame flows broker -> TCP -> add-on reader -> queue -> _on_timer -> RegionView3D.

This closes the loop on the only link the other probes don't exercise (the socket + reader thread).
No daemon, no trackball, no writes to the real %APPDATA% (it's redirected to a temp dir).

Run:
  blender --background --factory-startup --python tools\\blender_nav_socket_probe.py
"""
import json
import os
import sys
import tempfile
import time

# Redirect APPDATA so bridge.json + the add-on log land in a temp dir, not the real user dir.
_tmp = tempfile.mkdtemp(prefix="tbnav_probe_")
os.environ["APPDATA"] = _tmp
# The add-on resolves its own root, so build the discovery file where the daemon would write it.
_CONFIG_ROOT = os.path.join(_tmp, "Mildly Useful", "Astrolabe")
os.makedirs(_CONFIG_ROOT, exist_ok=True)
_PORT = 47913
with open(os.path.join(_CONFIG_ROOT, "bridge.json"), "w") as f:
    json.dump({"port": _PORT}, f)

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))                                   # trackball_daemon pkg
sys.path.insert(0, os.path.join(_HERE, "..", "trackball_daemon", "plugins", "blender"))  # add-on

import bpy                                                  # noqa: E402
import trackball_nav as tn                                  # noqa: E402
from trackball_daemon.navbroker import NavBroker            # noqa: E402

_fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


target = tn._resolve_target()
if target is None:
    print("SKIP no VIEW_3D realised in --background")
    print("ALL PASS")
    sys.exit(0)
rv = target[3]

broker = NavBroker(_PORT)
broker.start()
tn.register()                                               # starts the add-on's reader thread + timer
try:
    # wait for the add-on reader to connect to the broker
    connected = False
    for _ in range(100):
        if any(a == "blender" for a, _v, _p in broker.client_infos()):
            connected = True
            break
        time.sleep(0.05)
    check("socket.addon_connected", connected)
    print("clients:", broker.client_infos())

    broker.activate_target("blender")
    broker.set_scheme("blender", "camera", "free", "to_center",
                      advanced={"nav_mode": "orbit", "twist_action": "roll", "zoom_style": "zoom"})
    rot0 = rv.view_rotation.copy()
    broker.submit("blender", 0.3, 0.2, 0.0, 0.0, 0.0, 0.0,
                  state_revision=1)                          # one orbit delta on the BLE-thread side

    # the broker's sender thread flushes to the socket; drain on the main thread until the view moves
    applied = False
    for _ in range(100):
        time.sleep(0.05)
        tn._on_timer()
        if rv.view_rotation.rotation_difference(rot0).angle > 1e-4:
            applied = True
            break
    check("socket.frame_applied_to_view", applied)

    # nav_mode delivery, end-to-end: the same orbit delta behaves differently per nav_mode.
    #   orbit/object -> rotate about the selection (external pivot) => the EYE moves (orbits it)
    #   fly          -> rotate about the eye                        => the EYE stays put
    from mathutils import Vector  # noqa: E402
    for ob in bpy.context.scene.objects:           # select the default cube so "object" has a pivot
        try:
            ob.select_set(ob.type == 'MESH')
        except Exception:
            pass

    def submit_and_drain(op, adv):
        rot = rv.view_rotation.copy()
        broker.set_scheme("blender", op, "free", "to_center", advanced=adv)
        broker.submit("blender", 0.3, 0.2, 0.0, 0.0, 0.0, 0.0, state_revision=1)
        for _ in range(100):
            time.sleep(0.05)
            tn._on_timer()
            if rv.view_rotation.rotation_difference(rot).angle > 1e-4:
                return True
        return False

    rv.view_location = Vector((0.0, 0.0, 0.0)); rv.view_distance = 8.0
    eye0 = tn._eye(rv)
    submit_and_drain("object", {"nav_mode": "orbit"})
    check("socket.navmode_orbit_moves_eye", (tn._eye(rv) - eye0).length > 1e-3)

    rv.view_location = Vector((0.0, 0.0, 0.0)); rv.view_distance = 8.0
    eye0 = tn._eye(rv)
    submit_and_drain("camera", {"nav_mode": "fly"})
    check("socket.navmode_fly_keeps_eye", (tn._eye(rv) - eye0).length < 1e-3)
finally:
    tn.unregister()
    broker.stop()

print("\n%d failures" % len(_fails))
if _fails:
    print("FAILURES: " + ", ".join(_fails))
    sys.exit(1)
print("ALL PASS")
