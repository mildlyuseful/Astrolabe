"""Headless unit tests for the Fusion add-in's "cursor" pivot (pixel -> ray -> pivot).

Fusion has NO external automation, so the add-in normally can't be exercised outside the GUI at
all. TrackballNav.py only touches `adsk` through a small surface, though, so a stub `adsk` package
injected into sys.modules lets the module import headless and lets these tests drive the NEW
pixel->ray->pivot path with synthetic objects: a fake viewport (screenToView/viewToModelSpace), a
fake root component recording the findBRepUsingRay arguments, and a monkeypatched cursor.

What this PROVES offline: the ray construction (perspective ray = eye through the unprojected
pixel; ortho ray = parallel to the view axis through it, pushed back), the behind-the-eye guard,
the screenToView -> client-rect mapping chain, bbox validation, the per-gesture hold + fallbacks,
and that the refactor didn't change the "view" pivot. What it CANNOT prove (flagged in the add-in,
needs the Fusion GUI): the real coordinate spaces of screenToView/viewToModelSpace and live
cursor tracking.
"""
import os
import sys
import types

import pytest

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ADDIN = os.path.join(_HERE, "..", "trackball_daemon", "plugins", "fusion360", "TrackballNav")


# --- a minimal adsk stand-in, just enough for TrackballNav.py to import + run the pivot path ---
def _install_fake_adsk():
    class Point3D:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = float(x), float(y), float(z)

        @staticmethod
        def create(x, y, z):
            return Point3D(x, y, z)

        def copy(self):
            return Point3D(self.x, self.y, self.z)

    class Point2D:
        def __init__(self, x, y):
            self.x, self.y = float(x), float(y)

        @staticmethod
        def create(x, y):
            return Point2D(x, y)

    class Vector3D:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = float(x), float(y), float(z)

        @staticmethod
        def create(x, y, z):
            return Vector3D(x, y, z)

        @property
        def length(self):
            return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5

        def normalize(self):
            n = self.length
            if n > 0:
                self.x, self.y, self.z = self.x / n, self.y / n, self.z / n
            return True

        def crossProduct(self, o):
            return Vector3D(self.y * o.z - self.z * o.y,
                            self.z * o.x - self.x * o.z,
                            self.x * o.y - self.y * o.x)

    class Matrix3D:                       # _apply-only; unused by these tests
        @staticmethod
        def create():
            return Matrix3D()

    class ObjectCollection:
        def __init__(self):
            self._items = []

        @staticmethod
        def create():
            return ObjectCollection()

        def add(self, x):
            self._items.append(x)

        @property
        def count(self):
            return len(self._items)

        def item(self, i):
            return self._items[i]

    class CameraTypes:
        OrthographicCameraType = 0
        PerspectiveCameraType = 1

    class Application:
        @staticmethod
        def get():
            return None               # module-level `app` stays None; tests inject their own

    class CustomEventHandler:
        pass

    class Design:
        @staticmethod
        def cast(_x):
            return None

    class BRepEntityTypes:
        BRepFaceEntityType = 1

    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    for name, obj in (("Point3D", Point3D), ("Point2D", Point2D), ("Vector3D", Vector3D),
                      ("Matrix3D", Matrix3D), ("ObjectCollection", ObjectCollection),
                      ("CameraTypes", CameraTypes), ("Application", Application),
                      ("CustomEventHandler", CustomEventHandler)):
        setattr(core, name, obj)
    fusion.Design = Design
    fusion.BRepEntityTypes = BRepEntityTypes
    adsk.core, adsk.fusion = core, fusion
    sys.modules["adsk"] = adsk
    sys.modules["adsk.core"] = core
    sys.modules["adsk.fusion"] = fusion


_install_fake_adsk()
sys.path.insert(0, _ADDIN)
import TrackballNav as tn  # noqa: E402


# --- synthetic Fusion objects -------------------------------------------------------------
class FakeBB:
    def __init__(self, mn, mx):
        self.minPoint = tn.adsk.core.Point3D.create(*mn)
        self.maxPoint = tn.adsk.core.Point3D.create(*mx)


class FakeRoot:
    """Records every findBRepUsingRay call; returns configured hit points."""
    def __init__(self, hits=(), bbox=((-20, -20, -20), (20, 20, 20))):
        self.hits = list(hits)
        self.boundingBox = FakeBB(*bbox)
        self.calls = []

    def findBRepUsingRay(self, origin, direction, ent_type, tol, visible_only, out):
        self.calls.append(((origin.x, origin.y, origin.z),
                           (direction.x, direction.y, direction.z), tol))
        for h in self.hits:
            out.add(tn.adsk.core.Point3D.create(*h))


class FakeDesign:
    def __init__(self, root):
        self.rootComponent = root


class FakeVP:
    def __init__(self, width=1600, height=900, s2v=None, v2m=None):
        self.width, self.height = width, height
        self._s2v = s2v
        self._v2m = v2m

    def screenToView(self, p):
        if self._s2v is None:
            raise RuntimeError("no screenToView")
        return tn.adsk.core.Point2D.create(*self._s2v(p.x, p.y))

    def viewToModelSpace(self, p):
        return tn.adsk.core.Point3D.create(*self._v2m(p.x, p.y))


class FakeCam:
    def __init__(self, eye, tgt, extents=10.0, ortho=False):
        self.eye = tn.adsk.core.Point3D.create(*eye)
        self.target = tn.adsk.core.Point3D.create(*tgt)
        self.viewExtents = extents
        self.cameraType = (tn.adsk.core.CameraTypes.OrthographicCameraType if ortho
                           else tn.adsk.core.CameraTypes.PerspectiveCameraType)


def vclose(u, v, eps=1e-9):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    tn._gesture.update(t=0.0, pivot=None)
    tn._zoom_gesture.update(pivot=None)
    tn._obj_cache.update(t=0.0, p=None)
    monkeypatch.setattr(tn, "_log", lambda *a, **k: None)
    monkeypatch.setattr(tn, "_log_rl", lambda *a, **k: None)
    yield


def _wire(monkeypatch, vp, design, cursor=(800.0, 450.0), scale=1.0):
    monkeypatch.setattr(tn, "app", types.SimpleNamespace(activeViewport=vp))
    monkeypatch.setattr(tn, "_active_design", lambda: design)
    monkeypatch.setattr(tn, "_cursor_screen_pos", lambda: cursor)
    monkeypatch.setattr(tn, "_screen_scale", lambda sx, sy: scale)


# --- the screen -> viewport pixel mapping chain --------------------------------------------
def test_cursor_pixel_prefers_screen_to_view(monkeypatch):
    vp = FakeVP(s2v=lambda x, y: (x - 100.0, y - 50.0))       # in range for cursor (800,450)
    _wire(monkeypatch, vp, None)
    assert tn._cursor_view_pixel(vp) == (700.0, 400.0)


def test_cursor_pixel_falls_back_to_client_mapping(monkeypatch):
    vp = FakeVP(s2v=lambda x, y: (-5000.0, -5000.0))          # out of range -> distrust
    _wire(monkeypatch, vp, None)
    monkeypatch.setattr(tn, "_client_view_pixel", lambda sx, sy, v, s: (123.0, 456.0))
    assert tn._cursor_view_pixel(vp) == (123.0, 456.0)


def test_cursor_pixel_none_when_nothing_maps(monkeypatch):
    vp = FakeVP(s2v=None)                                     # screenToView raises
    _wire(monkeypatch, vp, None)
    monkeypatch.setattr(tn, "_client_view_pixel", lambda sx, sy, v, s: None)
    assert tn._cursor_view_pixel(vp) is None
    monkeypatch.setattr(tn, "_cursor_screen_pos", lambda: None)   # no cursor at all
    assert tn._cursor_view_pixel(vp) is None


def test_cursor_pixel_dpi_scaling(monkeypatch):
    # THE first user-reported live bug (0.1.11 -> 0.1.12): GetCursorPos is PHYSICAL px but
    # screenToView's INPUT is LOGICAL screen coords -- at 125% the pivot landed down-right of the
    # cursor. The physical cursor must be divided by the monitor scale BEFORE screenToView.
    seen = {}

    def s2v(x, y):
        seen["input"] = (x, y)
        return (x - 100.0, y - 50.0)

    vp = FakeVP(s2v=s2v)
    _wire(monkeypatch, vp, None, cursor=(1000.0, 500.0), scale=1.25)
    assert tn._cursor_view_pixel(vp) == (700.0, 350.0)       # (1000,500)/1.25 = (800,400) mapped
    assert seen["input"] == (800.0, 400.0)                    # logical, not physical


def test_cursor_pixel_right_band_uses_physical_bounds(monkeypatch):
    # THE second user-reported live bug (0.1.12 -> 0.1.13): screenToView's OUTPUT is PHYSICAL
    # viewport px while vp.width/height are LOGICAL, so validating against the logical size
    # wrongly rejected the right/bottom ~20% band at 125% ("fails to target and falls back").
    # A correct physical value beyond the logical size must be ACCEPTED and passed through
    # unscaled (viewToModelSpace consumes physical px).
    vp = FakeVP(width=1136, height=722, s2v=lambda x, y: (1300.0, 800.0))
    _wire(monkeypatch, vp, None, cursor=(1440.0, 1004.0), scale=1.25)
    # physical bounds = 1136*1.25 x 722*1.25 = 1420 x 902.5 -> (1300, 800) is IN range
    assert tn._cursor_view_pixel(vp) == (1300.0, 800.0)
    # but beyond the PHYSICAL bounds it is still rejected (genuinely off-viewport)
    vp2 = FakeVP(width=1136, height=722, s2v=lambda x, y: (1500.0, 800.0))
    _wire(monkeypatch, vp2, None, cursor=(1440.0, 1004.0), scale=1.25)
    monkeypatch.setattr(tn, "_client_view_pixel", lambda sx, sy, v, s: None)
    assert tn._cursor_view_pixel(vp2) is None


# --- ray construction: perspective from the eye, ortho parallel + pushed back --------------
def test_cursor_pivot_perspective_ray(monkeypatch):
    root = FakeRoot(hits=[(4.0, 2.4, 18.0)])
    vp = FakeVP(s2v=lambda x, y: (x, y), v2m=lambda x, y: (5.0, 3.0, 40.0))
    _wire(monkeypatch, vp, FakeDesign(root))
    cam = FakeCam(eye=(0, 0, 50), tgt=(0, 0, 0), extents=10.0)
    hit = tn._cursor_pivot(cam)
    assert hit is not None and vclose((hit.x, hit.y, hit.z), (4.0, 2.4, 18.0))
    origin, d, tol = root.calls[0]
    assert vclose(origin, (0.0, 0.0, 50.0))                   # ray starts at the eye
    n = (5.0 ** 2 + 3.0 ** 2 + 10.0 ** 2) ** 0.5              # pm - eye = (5,3,-10)
    assert vclose(d, (5.0 / n, 3.0 / n, -10.0 / n))
    assert abs(tol - tn.APERTURE_FRACS[0] * 5.0) < 1e-12      # half_h = extents/2

def test_cursor_pivot_ortho_ray(monkeypatch):
    root = FakeRoot(hits=[(5.0, 3.0, 17.0)])
    vp = FakeVP(s2v=lambda x, y: (x, y), v2m=lambda x, y: (5.0, 3.0, 40.0))
    _wire(monkeypatch, vp, FakeDesign(root))
    cam = FakeCam(eye=(0, 0, 50), tgt=(0, 0, 0), extents=10.0, ortho=True)
    hit = tn._cursor_pivot(cam)
    assert hit is not None and vclose((hit.x, hit.y, hit.z), (5.0, 3.0, 17.0))
    origin, d, _tol = root.calls[0]
    assert vclose(d, (0.0, 0.0, -1.0))                        # parallel to the view axis
    # pm pushed back RAY_PUSHBACK * half_h = 8 * 5 = 40 along -d
    assert vclose(origin, (5.0, 3.0, 40.0 + 40.0))


def test_cursor_pivot_behind_eye_is_rejected(monkeypatch):
    root = FakeRoot(hits=[(0.0, 0.0, 0.0)])
    vp = FakeVP(s2v=lambda x, y: (x, y), v2m=lambda x, y: (0.0, 0.0, 60.0))  # behind eye z=50
    _wire(monkeypatch, vp, FakeDesign(root))
    cam = FakeCam(eye=(0, 0, 50), tgt=(0, 0, 0))
    assert tn._cursor_pivot(cam) is None
    assert root.calls == []                                   # never raycast a bogus unproject


def test_cursor_pivot_bbox_rejects_stray_hit(monkeypatch):
    root = FakeRoot(hits=[(500.0, 0.0, 0.0)])                 # far outside the bbox
    vp = FakeVP(s2v=lambda x, y: (x, y), v2m=lambda x, y: (5.0, 3.0, 40.0))
    _wire(monkeypatch, vp, FakeDesign(root))
    cam = FakeCam(eye=(0, 0, 50), tgt=(0, 0, 0))
    assert tn._cursor_pivot(cam) is None
    assert len(root.calls) == len(tn.APERTURE_FRACS)          # tried every aperture


def test_cursor_pivot_none_without_pixel(monkeypatch):
    root = FakeRoot(hits=[(1.0, 1.0, 1.0)])
    vp = FakeVP(s2v=None, v2m=lambda x, y: (0.0, 0.0, 0.0))
    _wire(monkeypatch, vp, FakeDesign(root), cursor=None)
    monkeypatch.setattr(tn, "_cursor_screen_pos", lambda: None)
    assert tn._cursor_pivot(FakeCam(eye=(0, 0, 50), tgt=(0, 0, 0))) is None


# --- _orbit_pivot / _zoom_pivot: hold semantics + fallbacks --------------------------------
def _pt(x, y, z):
    return tn.adsk.core.Point3D.create(x, y, z)


def test_orbit_pivot_cursor_holds_and_recasts(monkeypatch):
    returns = [_pt(1, 1, 1), _pt(2, 2, 2)]
    monkeypatch.setattr(tn, "_cursor_pivot", lambda cam: returns.pop(0))
    monkeypatch.setattr(tn, "_object_center", lambda tgt: _pt(9, 9, 9))
    cam, tgt = object(), _pt(0, 0, 0)
    p1 = tn._orbit_pivot("cursor", cam, tgt, idle=10.0)
    assert (p1.x, p1.y, p1.z) == (1, 1, 1)
    p2 = tn._orbit_pivot("cursor", cam, tgt, idle=0.0)       # mid-gesture -> HELD
    assert p2 is p1
    p3 = tn._orbit_pivot("cursor", cam, tgt, idle=tn.PIVOT_HOLD_IDLE + 0.01)
    assert (p3.x, p3.y, p3.z) == (2, 2, 2)                    # gesture over -> re-cast


def test_orbit_pivot_cursor_falls_back_to_object_centre(monkeypatch):
    monkeypatch.setattr(tn, "_cursor_pivot", lambda cam: None)
    monkeypatch.setattr(tn, "_object_center", lambda tgt: _pt(9, 9, 9))
    p = tn._orbit_pivot("cursor", object(), _pt(0, 0, 0), idle=10.0)
    assert (p.x, p.y, p.z) == (9, 9, 9)


def test_orbit_pivot_view_still_uses_screen_centre(monkeypatch):
    # regression: the _raycast_pivot refactor must not change the "view" pivot's source
    monkeypatch.setattr(tn, "_screen_center_pivot", lambda cam: _pt(7, 7, 7))
    monkeypatch.setattr(tn, "_cursor_pivot",
                        lambda cam: (_ for _ in ()).throw(AssertionError("wrong path")))
    p = tn._orbit_pivot("view", object(), _pt(0, 0, 0), idle=10.0)
    assert (p.x, p.y, p.z) == (7, 7, 7)


def test_zoom_pivot_to_cursor_holds_and_falls_back(monkeypatch):
    returns = [_pt(3, 3, 3), _pt(4, 4, 4)]
    monkeypatch.setattr(tn, "_cursor_pivot", lambda cam: returns.pop(0))
    tgt = _pt(0, 0, 0)
    p1 = tn._zoom_pivot("to_cursor", object(), tgt, idle=10.0)
    assert (p1.x, p1.y, p1.z) == (3, 3, 3)
    assert tn._zoom_pivot("to_cursor", object(), tgt, idle=0.0) is p1     # held
    p3 = tn._zoom_pivot("to_cursor", object(), tgt, idle=tn.PIVOT_HOLD_IDLE + 0.01)
    assert (p3.x, p3.y, p3.z) == (4, 4, 4)
    # miss -> the view target (to_center behaviour)
    tn._zoom_gesture.update(pivot=None)
    monkeypatch.setattr(tn, "_cursor_pivot", lambda cam: None)
    assert tn._zoom_pivot("to_cursor", object(), tgt, idle=10.0) is tgt


def test_zoom_pivot_to_center_unchanged():
    tgt = _pt(0, 0, 0)
    assert tn._zoom_pivot("to_center", object(), tgt, idle=0.0) is tgt


def test_selection_override_uses_aggregate_selection_bounds(monkeypatch):
    class Selection:
        def __init__(self, bounds):
            self.entity = types.SimpleNamespace(boundingBox=FakeBB(*bounds))

    class Selections:
        def __init__(self, items):
            self._items = items
            self.count = len(items)

        def item(self, index):
            return self._items[index]

    selected = Selections([
        Selection(((-2.0, 0.0, 4.0), (2.0, 2.0, 6.0))),
        Selection(((8.0, -4.0, 0.0), (10.0, 4.0, 8.0))),
    ])
    monkeypatch.setattr(tn, "app", types.SimpleNamespace(activeSelections=selected))
    target = _pt(50.0, 50.0, 50.0)
    p = tn._orbit_pivot("origin", object(), target, idle=10.0, sel_override=True)
    assert (p.x, p.y, p.z) == pytest.approx((4.0, 0.0, 4.0))
    p = tn._orbit_pivot("origin", object(), target, idle=10.0, sel_override=False)
    assert (p.x, p.y, p.z) == pytest.approx((0.0, 0.0, 0.0))


def test_designated_selection_works_when_override_is_off(monkeypatch):
    selected = types.SimpleNamespace(
        count=1,
        item=lambda _i: types.SimpleNamespace(
            entity=types.SimpleNamespace(boundingBox=FakeBB((2, 4, 6), (6, 8, 10)))))
    monkeypatch.setattr(tn, "app", types.SimpleNamespace(activeSelections=selected))
    p = tn._orbit_pivot("selection", object(), _pt(50, 50, 50), idle=10.0,
                        sel_override=False)
    assert (p.x, p.y, p.z) == pytest.approx((4.0, 6.0, 8.0))
