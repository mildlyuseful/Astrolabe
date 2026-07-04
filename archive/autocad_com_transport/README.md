# Archived: the AutoCAD COM nav transport (daemon <= 0.1.37)

Archived 2026-07-03. These files are the complete COM *navigation transport* for AutoCAD —
the code that moved the viewport over ActiveX/COM (`ActiveViewport` reassign orbit, `Zoom*`
pan/zoom, the deferred-orbit batching, and the rotating-cube preview overlay) — plus their
tests, exactly as they shipped in daemon 0.1.37.

## Why it was retired

The compiled NETLOAD plugin (`plugin_src/autocad`, TrackballNavAcad.dll >= 0.2.8) drives the
viewport's live GraphicsSystem view in-process: ~1.6 ms/frame, zero regens, correct in every
visual style including 2D Wireframe. Once it was proven, keeping the COM transport as a
"fallback" caused real conflicts rather than adding safety:

- **The race**: on attach the COM driver connected FIRST (the plugin takes a moment to
  NETLOAD and handshake with the broker), so the first gesture of a session went through the
  regen-per-frame reassign path — flicker, the overlay cube, and a different feel.
- **The late orbit**: a deferred orbit accumulated during those first frames could apply
  ~100 ms AFTER the plugin took over the frames, fighting the GS view it now owned.
- **UI ambiguity**: the tray/apps line showed "autocad (COM)" before flipping to the plugin
  client, and the settings window's transport state was ambiguous during the overlap.

Daemon 0.1.38 removed the transport: AutoCAD frames route to the nav broker unconditionally
(like Fusion/Blender/FreeCAD), and `trackball_daemon/autocad_driver.py` was reduced to the
one job COM is still the right tool for — copying the bundled plugin to the runtime dir,
extending TRUSTEDPATHS, and NETLOADing it into a running AutoCAD (once per session).

## What is still valuable in here

- `autocad_driver.py`'s module docstring is the verified COM view model (ROT attach, sysvar
  ground truth, the reassign/regen ceiling, the treacherous `Center` property, SAFEARRAY
  marshalling). The same facts live in `docs/apps/autocad.md` §1–§8.13.
- `acad_overlay.py` is a self-contained click-through layered-window wireframe cube
  (pure Win32 via ctypes) — reusable for any "preview while the app catches up" need.

If a future AutoCAD-like target has no in-process plugin path, this is the proven COM
fallback pattern to resurrect.
