# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Headless unit tests for the FreeCAD add-on's "cursor" orbit pivot / "to_cursor" zoom.

tbnav_freecad imports only stdlib + tbnav_camera at module level (FreeCAD/pivy/PySide imports all
live inside functions), so the pivot RESOLVERS run under plain pytest with a synthetic view: a stub
whose getObjectInfo returns a known hit per pixel. This proves the offline math -- cursor pixel ->
raycast -> bbox-validated pivot, the per-gesture hold, and the fallbacks -- exactly as
tests/test_freecad_nav_math.py proves the camera math. What it CANNOT prove is that the
SoLocation2Event observer tracks the real mouse in a live viewport (see the live-probe notes in
docs/apps/freecad.md).
"""
import os
import sys
import types

# Don't write a __pycache__ into the bundled add-on dir (it's shipped package data).
sys.dont_write_bytecode = True

# Make the bundled add-on importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "trackball_daemon", "plugins", "freecad", "TrackballNav"))

import pytest  # noqa: E402

import tbnav_camera as cam  # noqa: E402
import tbnav_freecad as fc  # noqa: E402


# --- synthetic FreeCAD stand-ins ---------------------------------------------------------
class FakeBB:
    """Duck-typed FreeCAD BoundBox."""
    def __init__(self, mn, mx):
        (self.XMin, self.YMin, self.ZMin) = mn
        (self.XMax, self.YMax, self.ZMax) = mx

    def isValid(self):
        return True


class FakeShape:
    def __init__(self, bb):
        self.BoundBox = bb

    def isNull(self):
        return False


class FakeObj:
    def __init__(self, bb, parent=None):
        self.Shape = FakeShape(bb)
        self._parent = parent

    def getParentGeoFeatureGroup(self):
        return self._parent


class FakeDoc:
    def __init__(self, objects):
        self.Objects = objects


class FakeView:
    """Stub View3DInventorPy: getObjectInfo returns a known hit for a given pixel (None off-model),
    like the real call; getSize is only read by the y-flip path."""
    def __init__(self, size=(800, 600), hits=None):
        self._size = size
        self.hits = hits or {}
        self.pick_calls = []

    def getSize(self):
        return self._size

    def getObjectInfo(self, px):
        self.pick_calls.append(tuple(px))
        return self.hits.get(tuple(px))


def _hit(x, y, z):
    return {"x": x, "y": y, "z": z, "Object": "Box"}


def _ortho_cam():
    return cam.Camera(position=(0.0, 0.0, 100.0), orientation=(0.0, 0.0, 0.0, 1.0),
                      focal=100.0, height=20.0, is_ortho=True)


# One 10x10x10 box at the origin: centre (5,5,5), diagonal ~17.3 (bbox margin ~1.73).
DOC = FakeDoc([FakeObj(FakeBB((0, 0, 0), (10, 10, 10)))])
CENTER = (5.0, 5.0, 5.0)

PTR_PX = (100, 150)
CENTER_PX = (400, 300)
PTR_HIT = (2.0, 3.0, 4.0)
CENTER_HIT = (7.0, 8.0, 9.0)


def _view_both_hits():
    return FakeView(size=(800, 600), hits={PTR_PX: _hit(*PTR_HIT), CENTER_PX: _hit(*CENTER_HIT)})


def test_document_bbox_ignores_nested_local_space_features():
    container = FakeObj(FakeBB((100, 200, 300), (110, 220, 330)))
    child = FakeObj(FakeBB((0, 0, 0), (10, 20, 30)), parent=container)
    center, bbox = fc._doc_object_bbox(FakeDoc([container, child]))
    assert center == (105.0, 210.0, 315.0)
    assert bbox == ((100.0, 200.0, 300.0), (110.0, 220.0, 330.0))


@pytest.fixture(autouse=True)
def _reset_addon_state():
    fc._gesture.update(t=0.0, pivot=None)
    fc._zoom_gesture.update(pivot=None)
    fc._obj_cache.update(t=0.0, center=None, bbox=None)
    fc._cursor.update(px=None, t=0.0)
    fc._cursor_hook.update(
        view=None, checked=0.0, leave_filter=None, leave_widgets=[])
    yield


# --- the observer callback + hook lifecycle ----------------------------------------------
def test_cursor_event_cb_caches_pixel():
    class Pos:
        def getValue(self):
            return (123, 456)

    class Ev:
        def getPosition(self):
            return Pos()

    class EvCB:
        def getEvent(self):
            return Ev()

    fc._cursor_event_cb(EvCB())
    assert fc._cursor["px"] == (123, 456)
    assert fc._cursor["t"] > 0.0


def test_cursor_event_cb_never_raises():
    fc._cursor_event_cb(object())            # wrong shape -> swallowed, cache untouched
    assert fc._cursor["px"] is None


def test_cursor_invalidation_clears_pixel_and_both_cursor_derived_holds():
    fc._cursor.update(px=(12, 34), t=123.0)
    fc._gesture["pivot"] = (1.0, 2.0, 3.0)
    fc._zoom_gesture["pivot"] = (4.0, 5.0, 6.0)

    fc._invalidate_cursor("test_leave")

    assert fc._cursor == {"px": None, "t": 0.0}
    assert fc._gesture["pivot"] is None
    assert fc._zoom_gesture["pivot"] is None


def test_qt_leave_filter_invalidates_without_consuming_the_event(monkeypatch):
    class QObject:
        pass

    class QEvent:
        Leave = 17

    monkeypatch.setattr(fc, "_QtCore", types.SimpleNamespace(QObject=QObject, QEvent=QEvent))
    fc._cursor.update(px=(12, 34), t=123.0)
    event_filter = fc._make_cursor_leave_filter()

    consumed = event_filter.eventFilter(
        object(), types.SimpleNamespace(type=lambda: QEvent.Leave))

    assert consumed is False
    assert fc._cursor["px"] is None


class HookView:
    def __init__(self):
        self.added = []
        self.removed = []

    def addEventCallbackPivy(self, tid, cb):
        self.added.append((tid, cb))

    def removeEventCallbackPivy(self, tid, cb):
        self.removed.append((tid, cb))


@pytest.fixture()
def fake_pivy(monkeypatch):
    """A stand-in pivy so _ensure_cursor_hook's `from pivy import coin` works headless."""
    class SoLoc:
        @staticmethod
        def getClassTypeId():
            return "SoLocation2Event"

    pivy = types.ModuleType("pivy")
    pivy.coin = types.SimpleNamespace(SoLocation2Event=SoLoc)
    monkeypatch.setitem(sys.modules, "pivy", pivy)
    return pivy


def test_ensure_cursor_hook_registers_once(fake_pivy):
    v = HookView()
    fc._ensure_cursor_hook(v)
    fc._ensure_cursor_hook(v)                # same view -> no re-registration
    assert len(v.added) == 1 and v.added[0][1] is fc._cursor_event_cb
    assert fc._cursor_hook["view"] is v


def test_ensure_cursor_hook_rebinds_on_view_change(fake_pivy):
    v1, v2 = HookView(), HookView()
    fc._ensure_cursor_hook(v1)
    fc._cursor["px"] = (10, 20)
    fc._ensure_cursor_hook(v2)
    assert len(v1.removed) == 1               # observer detached from the old view
    assert len(v2.added) == 1
    assert fc._cursor_hook["view"] is v2
    assert fc._cursor["px"] is None          # old view's pixel is meaningless in the new one


def test_ensure_cursor_hook_without_pivy_is_a_noop(monkeypatch):
    monkeypatch.setitem(sys.modules, "pivy", None)   # import pivy -> ImportError
    v = HookView()
    fc._ensure_cursor_hook(v)                # must not raise
    assert v.added == [] and fc._cursor_hook["view"] is None


# --- _cursor_pivot: the raycast-under-the-cursor ---------------------------------------
def test_cursor_pivot_raycasts_cached_pixel_not_centre():
    view = _view_both_hits()
    fc._cursor.update(px=PTR_PX)
    bbox = ((0, 0, 0), (10, 10, 10))
    assert fc._cursor_pivot(view, bbox) == PTR_HIT
    assert fc._screen_center_pivot(view, bbox) == CENTER_HIT
    assert PTR_HIT != CENTER_HIT              # the cursor pixel changes the pivot


def test_cursor_pivot_none_without_cached_pixel():
    assert fc._cursor_pivot(_view_both_hits(), None) is None


def test_cursor_pivot_none_off_model():
    view = FakeView(hits={})                  # getObjectInfo -> None everywhere
    fc._cursor.update(px=PTR_PX)
    assert fc._cursor_pivot(view, None) is None


def test_cursor_pivot_rejects_hit_outside_bbox():
    view = FakeView(hits={PTR_PX: _hit(100.0, 100.0, 100.0)})
    fc._cursor.update(px=PTR_PX)
    assert fc._cursor_pivot(view, ((0, 0, 0), (10, 10, 10))) is None


def test_cursor_pivot_y_flip_path():
    # CURSOR_Y_FLIP=True must feed getObjectInfo (x, height - y). Default is False (Coin's
    # SoLocation2Event and getObjectInfo share the bottom-left origin); this locks the flip MATH
    # in case a FreeCAD change ever needs it turned on.
    view = FakeView(size=(800, 600), hits={(100, 450): _hit(*PTR_HIT)})
    fc._cursor.update(px=(100, 150))
    old = fc.CURSOR_Y_FLIP
    fc.CURSOR_Y_FLIP = True
    try:
        assert fc._cursor_pivot(view, None) == PTR_HIT
        assert view.pick_calls == [(100, 450)]
    finally:
        fc.CURSOR_Y_FLIP = old


# --- _orbit_pivot op=="cursor": per-gesture hold + fallbacks ------------------------------
def test_orbit_pivot_cursor_differs_from_screen_center():
    view = _view_both_hits()
    fc._cursor.update(px=PTR_PX)
    p_ptr = fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=10.0)
    fc._gesture.update(pivot=None)            # fresh gesture for the other scheme
    p_center = fc._orbit_pivot("screen_center", view, DOC, _ortho_cam(), idle=10.0)
    assert p_ptr == PTR_HIT and p_center == CENTER_HIT and p_ptr != p_center


def test_orbit_pivot_cursor_holds_for_the_gesture():
    view = _view_both_hits()
    view.hits[(200, 200)] = _hit(6.0, 6.0, 6.0)
    fc._cursor.update(px=PTR_PX)
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=10.0) == PTR_HIT
    fc._cursor.update(px=(200, 200))         # mouse moves MID-gesture...
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=0.0) == PTR_HIT   # ...held
    # after the idle gap (gesture over) the pivot re-raycasts at the new cursor position
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(),
                           idle=fc.PIVOT_HOLD_IDLE + 0.01) == (6.0, 6.0, 6.0)


def test_orbit_pivot_cursor_recasts_when_hold_cleared():
    # pan/zoom clear _gesture["pivot"] (see _apply); a cleared hold must re-raycast immediately
    view = _view_both_hits()
    fc._cursor.update(px=PTR_PX)
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=10.0) == PTR_HIT
    view.hits[PTR_PX] = _hit(1.0, 1.0, 1.0)
    fc._gesture.update(pivot=None)            # what the pan/zoom branches do
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=0.0) == (1.0, 1.0, 1.0)


def test_orbit_pivot_cursor_falls_back_to_object_centre_on_miss():
    view = FakeView(hits={})                  # nothing under the cursor
    fc._cursor.update(px=PTR_PX)
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=10.0) == CENTER
    # and with no cursor cached at all (mouse never over the viewport)
    fc._gesture.update(pivot=None)
    fc._cursor.update(px=None)
    assert fc._orbit_pivot("cursor", view, DOC, _ortho_cam(), idle=10.0) == CENTER


def test_orbit_pivot_cursor_last_resort_is_look_at():
    view = FakeView(hits={})
    empty_doc = FakeDoc([])                   # no model -> no centre either
    c = _ortho_cam()
    assert fc._orbit_pivot("cursor", view, empty_doc, c, idle=10.0) == cam.look_at(c)


def test_selection_override_wins_and_can_be_disabled(monkeypatch):
    monkeypatch.setattr(fc, "_selection_center", lambda _doc: (2.0, 3.0, 4.0))
    c = _ortho_cam()
    view = _view_both_hits()
    assert fc._orbit_pivot("origin", view, DOC, c, idle=10.0,
                           sel_override=True) == (2.0, 3.0, 4.0)
    assert fc._orbit_pivot("origin", view, DOC, c, idle=10.0,
                           sel_override=False) == (0.0, 0.0, 0.0)


def test_designated_selection_works_when_override_is_off(monkeypatch):
    monkeypatch.setattr(fc, "_selection_center", lambda _doc: (6.0, 7.0, 8.0))
    assert fc._orbit_pivot("selection", _view_both_hits(), DOC, _ortho_cam(), idle=10.0,
                           sel_override=False) == (6.0, 7.0, 8.0)


# --- _zoom_pivot zm=="to_cursor" ----------------------------------------------------------
def test_zoom_pivot_to_cursor_uses_cursor_hit():
    view = _view_both_hits()
    fc._cursor.update(px=PTR_PX)
    assert fc._zoom_pivot("to_cursor", view, DOC, idle=10.0) == PTR_HIT
    assert fc._zoom_pivot("to_center", view, DOC, idle=10.0) is None


def test_zoom_pivot_to_cursor_holds_per_gesture():
    view = _view_both_hits()
    view.hits[(200, 200)] = _hit(6.0, 6.0, 6.0)
    fc._cursor.update(px=PTR_PX)
    assert fc._zoom_pivot("to_cursor", view, DOC, idle=10.0) == PTR_HIT
    fc._cursor.update(px=(200, 200))
    assert fc._zoom_pivot("to_cursor", view, DOC, idle=0.0) == PTR_HIT          # held
    assert fc._zoom_pivot("to_cursor", view, DOC,
                          idle=fc.PIVOT_HOLD_IDLE + 0.01) == (6.0, 6.0, 6.0)     # re-cast


def test_zoom_pivot_to_cursor_miss_zooms_about_look_at():
    view = FakeView(hits={})
    fc._cursor.update(px=PTR_PX)
    assert fc._zoom_pivot("to_cursor", view, DOC, idle=10.0) is None
