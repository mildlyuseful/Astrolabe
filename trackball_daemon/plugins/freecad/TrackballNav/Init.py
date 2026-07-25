# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""TrackballNav -- console-mode init (intentionally a no-op).

This file exists ONLY so FreeCAD recognises the Mod folder: FreeCAD skips any Mod/<name>/
directory that has no Init.py, and would then never run InitGui.py either. The Trackball
navigation bridge is GUI-only (it drives the 3D view's camera), so there is nothing to do in
console mode (freecadcmd) -- all the work happens in InitGui.py -> tbnav_freecad.start().
"""
