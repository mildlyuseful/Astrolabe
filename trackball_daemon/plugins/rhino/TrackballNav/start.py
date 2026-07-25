#! python3
# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Startup entry for Trackball Nav in Rhino 8.

Installed under %%APPDATA%%\\McNeel\\Rhinoceros\\8.0\\scripts\\TrackballNav\\ and invoked by
Rhino's startup command (written by the daemon's Set up) or manually via:

    _-RunPythonScript "…\\TrackballNav\\start.py"
"""
from __future__ import print_function

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import tbnav_rhino
    tbnav_rhino.start()
except Exception as exc:
    try:
        import traceback
        # The current configuration root, falling back to an older daemon build's. tbnav_rhino
        # could not be imported, so this shim resolves it without help.
        roots = [os.path.join(os.environ.get("APPDATA", ""), *parts)
                 for parts in (("Mildly Useful", "Astrolabe"), ("TrackballDaemon",))]
        path = os.path.join(next((r for r in roots if os.path.isdir(r)), roots[0]),
                            "rhino_addin.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("start.py FAILED: %s\n%s\n" % (exc, traceback.format_exc()))
    except Exception:
        pass
