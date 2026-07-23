# AutoCAD viewport ownership live evidence — 2026-07-23

This record captures the live result that closed the viewport-ownership P0. Remaining qualification
work stays in [`../../TODO.md`](../../TODO.md).

## Environment and artifact

- Host: AutoCAD 2026.
- Artifact: bundled TrackballNavAcad v0.3.20, DLL SHA-256
  `accaa944b6f859f7fe35a07ef8ca10c04bb5356bf8743d3184679f75c7e791c4`.
- Plugin source: `88c233595a30764267b6c28ca909a3c702e2c235`; bundle commit:
  `9169fb45017e4aaeac7cbf8382f1dd99f99bf34e`.

## Exercise and result

- Model tab, 2D Wireframe: pass. Repeated Under Cursor orbit gestures used the current cursor target,
  rendered without high-frequency flicker or model snap-back, and retained the established
  CVPORT/VPORT commit behavior.
- Floating layout model viewport, 2D Wireframe: pass. Repeated orbit gestures retained their final
  camera without the wireframe geometry snapping independently from the ViewCube, axes, or grid.
- Layout transitions: pass. Moving through Paper space and returning to the floating model viewport
  retained working navigation without demoting the GraphicsSystem session.
- Other visual styles: pass in the same Model-tab and floating-layout exercise.

The live pass establishes the split ownership contract: Model-tab navigation remains keyed by its
numbered CVPORT/VPORT record, while a floating layout viewport is owned and persisted by its
`AcDbViewport` entity. The AutoCAD viewport-ownership P0 is closed.
