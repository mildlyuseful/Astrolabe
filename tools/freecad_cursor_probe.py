# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Live FreeCAD GUI probe for the "cursor" orbit pivot's half A (the live cursor pixel).

Run (it opens the real FreeCAD GUI briefly, then self-closes):

    & 'C:\\Program Files\\FreeCAD 1.1\\bin\\freecad.exe' tools\\freecad_cursor_probe.py

Results land in %TEMP%\\tbnav_cursor_probe.log (override with TBNAV_PROBE_LOG). Stdout is
useless for a FreeCAD GUI probe (see docs/apps/freecad.md §9) -- read the log.

What it verifies, by driving synthetic QMouseEvents through the REAL Qt -> Quarter -> Coin
pipeline (the same code path a physical mouse takes), so no human mouse is needed:
  1. `Gui.ActiveDocument.ActiveView` returns a STABLE Python object (`is`-identity) -- the
     add-on's _ensure_cursor_hook uses `is` to detect a view change.
  2. `addEventCallbackPivy(SoLocation2Event)` registers, FIRES on mouse moves, and what
     coordinate convention `getPosition()` reports (bottom-left vs top-left origin; logical vs
     device pixels vs `view.getSize()`).
  3. `view.getObjectInfo(<cached cursor pixel>)` hits the model at an off-centre pixel and the
     hit differs from the screen-centre hit (cursor != centre changes the pivot).

The one thing this cannot prove: the FEEL of a human hovering + orbiting simultaneously
(synthetic events are the same objects, but do a live pass with the trackball anyway).
"""
import os
import time
import traceback

LOG = os.environ.get("TBNAV_PROBE_LOG") or os.path.join(
    os.environ.get("TEMP", "."), "tbnav_cursor_probe.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def _post_move(QtCore, QtGui, wgt, x, y):
    """Post a synthetic MouseMove at widget-local (x, y) -- same event a real mouse produces."""
    lp = QtCore.QPointF(float(x), float(y))
    gp = QtCore.QPointF(wgt.mapToGlobal(QtCore.QPoint(int(x), int(y))))
    try:
        ev = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, lp, gp,
                               QtCore.Qt.MouseButton.NoButton,
                               QtCore.Qt.MouseButton.NoButton,
                               QtCore.Qt.KeyboardModifier.NoModifier)
    except Exception:                       # newer overload (adds scenePos)
        ev = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, lp, lp, gp,
                               QtCore.Qt.MouseButton.NoButton,
                               QtCore.Qt.MouseButton.NoButton,
                               QtCore.Qt.KeyboardModifier.NoModifier)
    QtCore.QCoreApplication.postEvent(wgt, ev)


def _fmt_hit(info):
    if not info:
        return "MISS"
    try:
        return "(%.3f, %.3f, %.3f) on %s" % (float(info["x"]), float(info["y"]),
                                             float(info["z"]), info.get("Object"))
    except Exception:
        return "odd dict: %r" % (info,)


def _run():
    import FreeCAD as App
    import FreeCADGui as Gui
    from pivy import coin
    from PySide6 import QtCore, QtGui, QtWidgets

    log("probe start (FreeCAD %s)" % ".".join(str(x) for x in App.Version()[:3]))

    doc = App.newDocument("PtrProbe")
    box = doc.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = 10.0
    doc.recompute()
    view = Gui.ActiveDocument.ActiveView
    view.viewIsometric()
    view.fitAll()
    Gui.updateGui()

    # --- 1. ActiveView identity stability (what _ensure_cursor_hook's `is` check needs)
    log("ActiveView identity stable across reads: %r"
        % (view is Gui.ActiveDocument.ActiveView))

    # --- 2. the passive SoLocation2Event observer
    cache = {"n": 0}

    def cb(event_cb):
        try:
            pos = event_cb.getEvent().getPosition().getValue()
            cache["px"] = (int(pos[0]), int(pos[1]))
            cache["n"] += 1
        except Exception:
            cache["err"] = traceback.format_exc()

    view.addEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), cb)
    log("addEventCallbackPivy(SoLocation2Event) registered without error")

    size = view.getSize()
    log("view.getSize() = (%d, %d)" % (int(size[0]), int(size[1])))

    mw = Gui.getMainWindow()
    targets = []
    for w in mw.findChildren(QtWidgets.QWidget):
        cn = w.metaObject().className()
        if ("Quarter" in cn or "View3DInventor" in cn) and w.width() > 100:
            targets.append((cn, w))
    log("candidate 3D widgets: %s"
        % [(cn, w.width(), w.height(), round(w.devicePixelRatioF(), 2)) for cn, w in targets])

    fired = None
    for cn, w in targets:
        recv = w.viewport() if isinstance(w, QtWidgets.QGraphicsView) else w
        W, H = recv.width(), recv.height()
        for label, (qx, qy) in (("near-TOP", (W // 2, 10)), ("near-BOTTOM", (W // 2, H - 10))):
            cache.pop("px", None)
            n0 = cache["n"]
            _post_move(QtCore, QtGui, recv, qx, qy)
            QtWidgets.QApplication.processEvents()
            got = cache.get("px")
            log("post MouseMove %s qt=(%d,%d) -> target=%s%s : cb %s, cached px=%s"
                % (label, qx, qy, cn, "(viewport)" if recv is not w else "",
                   "FIRED" if cache["n"] > n0 else "did NOT fire", got))
            if cache["n"] > n0 and got is not None:
                fired = (cn, recv, W, H, (qx, qy), got)
        if fired:
            break
    if "err" in cache:
        log("cb error: " + cache["err"].strip().replace("\n", " | "))

    # --- 3. raycast the cached cursor pixel vs the screen centre
    cx, cy = int(size[0] / 2), int(size[1] / 2)
    centre_hit = view.getObjectInfo((cx, cy))
    log("getObjectInfo(centre (%d,%d)) = %s" % (cx, cy, _fmt_hit(centre_hit)))

    if fired:
        cn, recv, W, H, (qx, qy), _ = fired
        # aim an OFF-CENTRE pixel at the box: 60%% across, 40%% down in Qt widget coords
        qx2, qy2 = int(W * 0.60), int(H * 0.40)
        cache.pop("px", None)
        _post_move(QtCore, QtGui, recv, qx2, qy2)
        QtWidgets.QApplication.processEvents()
        px = cache.get("px")
        log("off-centre post qt=(%d,%d) -> cached px=%s   [bottom-left origin would be y=%d, "
            "top-left passthrough y=%d]" % (qx2, qy2, px, H - 1 - qy2, qy2))
        if px is not None:
            ptr_hit = view.getObjectInfo((int(px[0]), int(px[1])))
            flip_hit = view.getObjectInfo((int(px[0]), int(size[1]) - int(px[1])))
            log("getObjectInfo(cached px UNFLIPPED %s) = %s" % (px, _fmt_hit(ptr_hit)))
            log("getObjectInfo(cached px Y-FLIPPED  (%d,%d)) = %s"
                % (int(px[0]), int(size[1]) - int(px[1]), _fmt_hit(flip_hit)))
            if ptr_hit and centre_hit:
                diff = any(abs(float(ptr_hit[k]) - float(centre_hit[k])) > 1e-6 for k in "xyz")
                log("cursor hit differs from centre hit: %r" % diff)
    else:
        log("NO candidate widget produced a SoLocation2Event -- observer NOT verified")

    log("probe done")


def _quit():
    try:
        import FreeCAD as App
        import FreeCADGui as Gui
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        Gui.getMainWindow().close()
    except Exception:
        log("quit error: " + traceback.format_exc().strip().replace("\n", " | "))


def _main():
    try:
        _run()
    except Exception:
        log("FATAL: " + traceback.format_exc())
    finally:
        try:
            from PySide6 import QtCore
            QtCore.QTimer.singleShot(1000, _quit)
        except Exception:
            pass


try:
    from PySide6 import QtCore as _QtCore
    _QtCore.QTimer.singleShot(3500, _main)   # defer until the GUI is fully up (GuiUp race)
    log("scheduled probe in 3500 ms")
except Exception:
    log("bootstrap FATAL: " + traceback.format_exc())
