# Per-app setting and feature parity

`trackball_daemon/binding_schema.py` is the UI contract. A control is enabled only when the
integration has a distinct runtime behavior for every option it presents. Stored config fields may
exist in the shared shape without being visible; that does not make them an implemented feature.

## Audited behavior

- Every app exposes its implemented Orbit style, Orbit pivot, Twist action, and zoom target.
- **Model Center** and **To Object** mean the aggregate project/model bounding-box center, not the
  current selection. **Selection** remains a separate orbit pivot.
- **To Cursor** uses a real surface hit when available and otherwise synthesizes a point on the
  cursor ray at the current target/focus/model depth. Where an app supports selection override for
  cursor zoom, that override does not replace **To Object**.
- **Pan-mode zoom: Zoom / Dolly** appears in Blender, Fusion 360, SketchUp, Unreal, Unity, Godot,
  Rhino, and AutoCAD. Zoom changes the native projection field/lens; Dolly moves the camera. In an
  orthographic viewport a physical dolly does not change magnification, by definition.
- **Pivot hold** appears in the Orbit section for every app. It controls the orbit-pivot idle gap;
  pan invalidates that pivot immediately. **Zoom hold** is separate and applies only to To Cursor
  zoom; pan preserves its target, while To Center and To Object do not use the timer.
- Godot is still turntable-only because its editor camera cannot persist a rolled/free basis. It now
  has distinct projection Zoom and camera Dolly paths, but no Free orbit or Roll option.
- Fusion's Twist action offers **Roll / Zoom / None**. Zoom enters the selected pan-mode Zoom/Dolly
  behavior; a separate Dolly twist option would be a duplicate, not a distinct action.

## Deliberately not exposed yet

These hosts have an API or internal math that could support more, but no complete feature exists in
the integration. They remain future work rather than speculative UI:

- **Camera (turn-in-place) orbit:** Fusion 360, FreeCAD, and SolidWorks. Their camera/view APIs are
  perspective-capable, but their current pivot resolvers deliberately skip Camera. Onshape also
  omits Camera because the supported orthographic view makes eye-pivot rotation degenerate into an
  image slide rather than a useful look operation.
- **Manual Zoom versus Dolly selection:** FreeCAD, Onshape, and SolidWorks retain their existing
  single/projection-selected behavior. A second user-selectable path has not been implemented there.
- **Godot Free orbit/roll:** the shared camera-math helper retains a free-rotation branch for tests,
  but the editor viewport integration always writes a yaw/pitch cursor with no roll. Only Turntable
  is advertised.

When adding one of these features, implement and verify the host behavior first, then enable its
field/options in `APP_BINDING_PROFILES` in the same change.
