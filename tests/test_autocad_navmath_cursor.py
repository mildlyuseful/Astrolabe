"""Offline unit test for the AutoCAD plugin's NavMath "cursor" pivot path.

The plugin's camera math (plugin_src/autocad/TrackballNavAcad/NavMath.cs) is pure but written
against AutoCAD's managed geometry types, and acdbmgd.dll cannot load outside acad.exe -- so the
offline test compiles NavMath.cs VERBATIM against minimal stub types and asserts the pivot
invariants with a synthetic cached point (plugin_src/autocad/NavMathTests/: rigid orbit about the
pivot for free + turntable, the pivot's screen position fixed, pivot==target reduces to the old
behaviour, to_cursor parallel zoom keeps the point's screen fraction fixed, perspective falls
back to the plain dolly).

Needs the .NET 8 SDK (present on the dev box; `dotnet build` of the plugin needs it anyway) --
skips cleanly where it's absent so the suite stays green on machines without it.
"""
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJ = os.path.join(_ROOT, "plugin_src", "autocad", "NavMathTests")


@pytest.mark.skipif(shutil.which("dotnet") is None, reason="no .NET SDK on this machine")
def test_navmath_cursor_pivot_invariants():
    res = subprocess.run(
        ["dotnet", "run", "-c", "Release", "--project", _PROJ],
        capture_output=True, text=True, timeout=300,
    )
    assert res.returncode == 0, (
        "NavMath cursor-pivot tests failed:\n" + res.stdout + res.stderr)
    assert "ALL PASS" in res.stdout
