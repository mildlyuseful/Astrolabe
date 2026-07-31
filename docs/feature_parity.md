# Per-app setting and feature parity

`trackball_daemon/app_registry.py` and `trackball_daemon/settings_schema.py` are the UI contract. A control is enabled only when the
integration has a distinct runtime behavior for every option it presents. Stored config fields may
exist in the shared shape without being visible; that does not make them an implemented feature.
Shared capability and routing ownership is defined in [`architecture.md`](architecture.md).

## Audited behavior

- Every app exposes its implemented Orbit style, Orbit pivot, Twist action, and zoom target.
- **Model Center** and **To Object** mean the aggregate project/model bounding-box center, not the
  current selection. **Selection** remains a separate orbit pivot.
- **To Cursor** uses a real surface hit when available and otherwise synthesizes a point on the
  cursor ray at the current target/focus/model depth. Where an app supports selection override for
  cursor zoom, that override does not replace **To Object**.
- **Pan-mode zoom: Zoom / Dolly** appears in Blender, Fusion 360, SketchUp, Unreal, Unity,
  Rhino, and AutoCAD. Zoom changes the native projection field/lens; Dolly moves the camera. In an
  orthographic viewport a physical dolly does not change magnification, by definition.
- **Pivot hold** appears in the Orbit section for every app. It controls the orbit-pivot idle gap;
  pan invalidates that pivot immediately. **Zoom hold** is separate and applies only to To Cursor
  zoom; pan preserves its target, while To Center and To Object do not use the timer.
- Fusion's Twist action offers **Roll / Zoom / None**. Zoom enters the selected pan-mode Zoom/Dolly
  behavior; a separate Dolly twist option would be a duplicate, not a distinct action.

Open parity work and host-accounted limitations live in [`TODO.md`](../TODO.md). When adding one of
those features, implement and verify the host behavior first, then update the canonical `APP_SPECS`
declaration through `_spec` / `_profile` and the matching `settings_schema.py` capability predicate in
the same change. `APP_BINDING_PROFILES` is a generated read-only compatibility view, not an authority.
