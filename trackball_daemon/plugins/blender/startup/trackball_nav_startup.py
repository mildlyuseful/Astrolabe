"""Trackball Nav -- auto-enable shim (installed by the Trackball Daemon).

Blender does NOT auto-enable an add-on just because its files were copied into
scripts/addons/. This startup script -- the analogue of Fusion's "Run on Startup" -- enables
the bundled `trackball_nav` add-on on every Blender launch so the daemon works zero-click.

The daemon writes this file (with your confirmation) into
  %APPDATA%\Blender Foundation\Blender\<ver>\scripts\startup\
To opt out of auto-start, delete this file; the add-on stays installed and can be toggled by
hand in Preferences -> Add-ons (search "Trackball").

We defer the enable to a one-shot timer so it runs out of Blender's restricted startup context.
"""
import bpy


def _enable():
    try:
        import addon_utils
        addon_utils.enable("trackball_nav", default_set=True, persistent=True)
    except Exception as exc:        # never break a user's Blender startup over this
        print("[trackball_nav] auto-enable failed:", exc)
    return None                     # one-shot: do not reschedule


try:
    bpy.app.timers.register(_enable, first_interval=0.1)
except Exception as exc:
    print("[trackball_nav] could not schedule auto-enable:", exc)
