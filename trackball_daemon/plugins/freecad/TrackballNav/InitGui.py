# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""TrackballNav -- FreeCAD GUI bootstrap (thin shim).

FreeCAD runs this file at GUI startup for every Mod/<name>/ folder. IMPORTANT: FreeCAD execs
InitGui.py with SEPARATE globals/locals dicts, so a function defined *here* cannot see names
defined *here* at module level (its __globals__ won't contain them). Putting the add-on logic
in this file therefore breaks silently. The fix: keep this file a shim that only IMPORTS the
real add-on module (Python imports it with a normal namespace) and calls its entry point.

The companion Init.py must exist too -- FreeCAD ignores a Mod folder that has no Init.py, so
InitGui.py would never run without it.
"""
try:
    import tbnav_freecad
    tbnav_freecad.start()
except Exception:
    # Never break FreeCAD's startup over this; record why instead.
    import os
    import time
    import traceback
    try:
        # The current configuration root, falling back to an older daemon build's. tbnav_freecad
        # could not be imported, so this shim resolves it without help.
        _roots = [os.path.join(os.environ.get("APPDATA", ""), *_parts)
                  for _parts in (("Mildly Useful", "Astrolabe"), ("TrackballDaemon",))]
        _p = os.path.join(next((_r for _r in _roots if os.path.isdir(_r)), _roots[0]),
                          "freecad_addin.log")
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(time.strftime("%H:%M:%S ") + "InitGui shim error: "
                     + traceback.format_exc().replace("\n", " | ") + "\n")
    except Exception:
        pass
