# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""TrackballNav -- Unreal Editor startup shim.

Unreal auto-runs ``init_unreal.py`` from every enabled plugin's ``Content/Python`` directory at
editor startup (the Python Editor Script Plugin must be enabled -- our .uplugin lists it as a
dependency so enabling Trackball Nav enables it too). This file is a thin shim that imports the
real add-on module (which gets a normal namespace) and starts it -- mirroring the FreeCAD
InitGui.py pattern. Keep the logic in trackball_nav.py, not here.
"""
try:
    import trackball_nav
    trackball_nav.start()
except Exception:
    # Never break the editor's Python startup over this; record why instead.
    import os
    import time
    import traceback
    try:
        _p = os.path.join(os.environ.get("APPDATA", ""), "TrackballDaemon", "unreal_addin.log")
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(time.strftime("%H:%M:%S ") + "init_unreal shim error: "
                     + traceback.format_exc().replace("\n", " | ") + "\n")
    except Exception:
        pass
