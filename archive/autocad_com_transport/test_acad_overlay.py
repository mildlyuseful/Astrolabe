# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Pure-math tests for the AutoCAD deferred-orbit overlay's cube projection.

Only project_cube (platform-independent) is tested -- no window is ever created here; the Win32
layered-window part is exercised live (and is disabled by the driver on its first failure anyway).
"""
import math

from trackball_daemon.acad_overlay import project_cube


def test_projection_shape_and_bounds():
    segs = project_cube((-1.0, -1.0, 1.0))          # SW-iso-ish direction
    assert len(segs) == 15                          # 12 cube edges + 3 axis lines
    kinds = [s[4] for s in segs]
    assert kinds.count("cube") == 12
    assert {"x", "y", "z"} <= set(kinds)
    for x1, y1, x2, y2, _kind in segs:
        for v in (x1, y1, x2, y2):
            assert math.isfinite(v)
            assert abs(v) <= 1.0001                 # everything fits the unit box


def test_projection_rotates_with_direction():
    a = project_cube((-1.0, -1.0, 1.0))
    b = project_cube((0.5, -1.2, 1.0))
    assert a != b                                   # the cube visibly turns with the view direction


def test_top_view_is_degenerate_safe():
    # Looking straight down world Z (the degenerate case): the Z axis projects to ~a point at the
    # centre, while X spans the screen plane -- and nothing blows up.
    segs = project_cube((0.0, 0.0, 1.0))
    zseg = next(s for s in segs if s[4] == "z")
    assert abs(zseg[2]) < 1e-9 and abs(zseg[3]) < 1e-9
    xseg = next(s for s in segs if s[4] == "x")
    assert abs(xseg[2]) > 0.5                       # X axis clearly visible
