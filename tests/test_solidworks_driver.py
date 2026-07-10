"""Unit tests for the SolidWorks COM driver -- everything except the live COM boundary.

The COM layer is mocked: _flush() runs against a fake IModelView / IModelDoc2, and attach
runs against a monkeypatched win32com.client.GetActiveObject. No SolidWorks is required.
What still needs a live SolidWorks: the real attach, and that orbit/pan/zoom directions +
sensitivities feel right (see README.md and the module's tuning constants).
"""
import math
import time

import pytest

from trackball_daemon import solidworks_driver
from trackball_daemon.solidworks_driver import SolidWorksDriver

requires_pywin32 = pytest.mark.skipif(
    not solidworks_driver._PYWIN32, reason="pywin32 not installed")


# --- fakes mimicking the SolidWorks COM objects the driver touches ---------------------
class FakeVector:
    def __init__(self, data):
        self.data = list(data)

    @property
    def ArrayData(self):
        return tuple(self.data)


class FakeMathUtil:
    def __init__(self):
        self.created = []

    def CreateVector(self, arr):
        self.created.append(list(arr))
        return FakeVector(arr)


class FakeTransform:
    """Stands in for IMathTransform. ArrayData is 16 doubles: a 3x3 row-major rotation, then
    translation (3), scale (1), pad (3). The default is the identity view->model orientation,
    whose rows are the model basis vectors -> camera axes == (1,0,0)/(0,1,0)/(0,0,1)."""

    def __init__(self, array_data=None):
        self.ArrayData = tuple(array_data) if array_data is not None else (
            1.0, 0.0, 0.0,  0.0, 1.0, 0.0,  0.0, 0.0, 1.0,  0.0, 0.0, 0.0,  1.0,  0.0, 0.0, 0.0)


class FakeView:
    """Records the native view calls the driver makes (RotateAboutAxis / Translation3 set /
    ZoomByFactor) and serves Orientation3.ArrayData (so orbit can be checked as camera-relative)
    and Translation3 (so pan can be checked). Set orient_raises=True to simulate a failing orbit."""

    def __init__(self, scale=4.0):
        self.rotations = []
        self.translation_sets = []  # IMathVectors assigned to Translation3 (pan)
        self.zooms = []             # factors passed to ZoomByFactor
        self.Scale2 = scale
        self._orient = FakeTransform()
        self.orient_reads = 0
        self.orient_raises = False
        self._translation = FakeVector([0.0, 0.0, 0.0])
        self._gfx = True
        self.graphics_update_calls = []   # values assigned to EnableGraphicsUpdate (freeze/resume)

    @property
    def EnableGraphicsUpdate(self):
        return self._gfx

    @EnableGraphicsUpdate.setter
    def EnableGraphicsUpdate(self, val):
        self._gfx = val
        self.graphics_update_calls.append(val)

    @property
    def Orientation3(self):
        self.orient_reads += 1
        if self.orient_raises:
            raise RuntimeError("Orientation3 boom")
        return self._orient

    @property
    def Translation3(self):
        return self._translation

    @Translation3.setter
    def Translation3(self, v):
        self._translation = v
        self.translation_sets.append(v)

    def RotateAboutAxis(self, angle, px, py, pz, ax, ay, az):
        self.rotations.append((angle, px, py, pz, ax, ay, az))

    def ZoomByFactor(self, factor):
        self.zooms.append(factor)
        self.Scale2 *= factor               # ZoomByFactor multiplies the view scale (verified live)


class FakeEntity:
    """A selected SW entity. Select(append) re-adds it to the selection (used by restore)."""

    def __init__(self, point, selmgr):
        self.point = tuple(point)
        self._selmgr = selmgr
        self.select_appends = []        # the `append` flag of each Select() call

    def _FlagAsMethod(self, name):
        pass

    def Select(self, append):
        self.select_appends.append(append)
        if not append:
            self._selmgr._sel = []
        self._selmgr._sel.append(self)
        return True


class FakeSelMgr:
    """Stands in for ISelectionMgr. _sel is the live selection list of FakeEntity."""

    def __init__(self):
        self._sel = []

    def _FlagAsMethod(self, name):
        pass

    def GetSelectedObjectCount2(self, mark):
        return len(self._sel)

    def GetSelectedObject6(self, index, mark):
        return self._sel[index - 1]

    def GetSelectionPoint2(self, index, mark):
        return self._sel[index - 1].point


class FakeExtension:
    """Stands in for IModelDocExtension.SelectByRay. `hits` is a list of (min_radius, point): a ray
    of the given radius selects every hit whose min_radius <= radius (models the expanding aperture
    and multi-hit cases). Records each call's args so tests can assert the marshaling (e.g. int Tol)."""

    def __init__(self, selmgr, hits=None):
        self._selmgr = selmgr
        self._hits = list(hits or [])
        self.ray_calls = []

    def _FlagAsMethod(self, name):
        pass

    def SelectByRay(self, x, y, z, vx, vy, vz, radius, tol, opt, append, mark):
        val = lambda a: getattr(a, "value", a)      # unwrap VARIANT (pywin32) or pass through
        r = float(val(radius))
        self.ray_calls.append({"radius": r, "tol": val(tol), "append": val(append),
                               "origin": (float(val(x)), float(val(y)), float(val(z))),
                               "dir": (float(val(vx)), float(val(vy)), float(val(vz)))})
        chosen = [pt for (min_r, pt) in self._hits if min_r <= r]
        if not chosen:
            return False
        if not bool(val(append)):
            self._selmgr._sel = []
        for pt in chosen:
            self._selmgr._sel.append(FakeEntity(pt, self._selmgr))
        return True


class FakeModel:
    """doc_type: 1=part (GetPartBox), 2=assembly (GetBox). box: 6-tuple bounding box or None.
    ray_hits: list of (min_radius, point) for the screen-centre raycast (see FakeExtension)."""

    def __init__(self, view, doc_type=1, box=None, ray_hits=None):
        self._view = view
        self.redraws = 0
        self.GetType = doc_type             # read as a property by the driver
        self._box = box
        self._selmgr = FakeSelMgr()
        self._ext = FakeExtension(self._selmgr, hits=ray_hits)
        self.clear_calls = 0

    def _FlagAsMethod(self, name):
        pass

    def GetPartBox(self, arg):
        if self._box is None:
            raise RuntimeError("no box")
        return self._box

    def GetBox(self, arg):
        if self._box is None:
            raise RuntimeError("no box")
        return self._box

    @property
    def Extension(self):
        return self._ext

    @property
    def SelectionManager(self):
        return self._selmgr

    def ClearSelection2(self, flag):
        self.clear_calls += 1
        self._selmgr._sel = []

    @property
    def ActiveView(self):
        return self._view

    def GraphicsRedraw2(self):
        self.redraws += 1


class FakeApp:
    def __init__(self, model):
        self._model = model
        self.RevisionNumber = "32.3.0"

    @property
    def ActiveDoc(self):
        return self._model

    def GetMathUtility(self):
        return FakeMathUtil()


def _attach_fakes(drv, doc_type=1, box=None, ray_hits=None):
    view = FakeView()
    model = FakeModel(view, doc_type=doc_type, box=box, ray_hits=ray_hits)
    drv._swApp = FakeApp(model)
    drv._mathUtil = FakeMathUtil()
    drv._mkvec = lambda x, y, z: FakeVector([x, y, z])   # bypass pywin32 VARIANT/CreateVector
    return model, view


# --- submit / accumulate --------------------------------------------------------------
def test_submit_accumulates():
    drv = SolidWorksDriver()
    drv.submit(1, 2, 3, 4, 5, 6)
    drv.submit(0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    assert drv._acc == [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]


# --- rate clamp / set_rate ------------------------------------------------------------
def test_clamp_rate_bounds_and_fallback():
    assert SolidWorksDriver._clamp_rate(0) == 1.0           # min
    assert SolidWorksDriver._clamp_rate(10_000) == 240.0    # max
    assert SolidWorksDriver._clamp_rate(30) == 30.0
    assert SolidWorksDriver._clamp_rate("nope") == solidworks_driver.DEFAULT_FLUSH_HZ


def test_set_rate_updates_period():
    drv = SolidWorksDriver()
    drv.set_rate(60)
    assert drv._period == pytest.approx(1.0 / 60.0)
    drv.set_rate(0)                                          # clamped up to 1 Hz
    assert drv._period == pytest.approx(1.0)


# --- flush: orbit / pan / zoom / redraw -----------------------------------------------
def test_flush_orbit_is_one_camera_relative_call():
    drv = SolidWorksDriver()
    model, view = _attach_fakes(drv)
    drv._flush((0.1, 0.2, 0.3, 0.0, 0.0, 0.0))
    assert view.orient_reads >= 1               # consulted Orientation3 => camera-relative
    assert len(view.rotations) == 1             # composed into ONE rotation/frame (no jitter)
    vx = solidworks_driver.ORBIT_SIGN[0] * 0.1
    vy = solidworks_driver.ORBIT_SIGN[1] * 0.2
    vz = solidworks_driver.ORBIT_SIGN[2] * 0.3
    angle = math.sqrt(vx * vx + vy * vy + vz * vz)
    r = view.rotations[0]
    assert r[0] == pytest.approx(angle)         # angle = magnitude of the camera-space axis-angle
    # with the identity orientation the model-space axis is the normalized (vx,vy,vz)
    assert (r[4], r[5], r[6]) == pytest.approx((vx / angle, vy / angle, vz / angle))
    assert model.redraws == 1


def test_flush_orbit_uses_columns_of_orientation():
    # Camera axes are the COLUMNS of Orientation3 (verified live), not the rows. Use a cyclic-
    # permutation orientation where row0=(0,0,1) but col0=(0,1,0), so a pure-ox (camera-right)
    # orbit must rotate about Y (col0), not Z (row0).
    drv = SolidWorksDriver()
    _model, view = _attach_fakes(drv)
    view._orient = FakeTransform((0.0, 0.0, 1.0,  1.0, 0.0, 0.0,  0.0, 1.0, 0.0,
                                  0.0, 0.0, 0.0,  1.0,  0.0, 0.0, 0.0))
    drv._flush((0.3, 0.0, 0.0, 0.0, 0.0, 0.0))             # pure camera-right orbit
    r = view.rotations[0]
    axis = (round(r[4], 6), round(r[5], 6), round(r[6], 6))
    assert axis[0] == 0.0 and axis[2] == 0.0 and abs(axis[1]) == 1.0  # along col0=(0,1,0), not row0=(0,0,1)


def test_flush_zero_orbit_does_not_rotate():
    drv = SolidWorksDriver()
    _model, view = _attach_fakes(drv)
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.7))              # zoom only -> no orbit call
    assert view.rotations == []


def test_flush_zoom_uses_zoombyfactor():
    drv = SolidWorksDriver()
    _model, view = _attach_fakes(drv)
    drv._flush((0, 0, 0, 0, 0, 0.5))
    expected = 1.0 + solidworks_driver.ZOOM_SIGN * 0.5 * solidworks_driver.ZOOM_SCALE
    assert view.zooms == [pytest.approx(expected)]


def test_flush_zoom_guards_nonpositive_factor():
    drv = SolidWorksDriver()
    _model, view = _attach_fakes(drv)
    drv._flush((0, 0, 0, 0, 0, -1000.0))                    # factor would be <= 0
    assert view.zooms == []                                 # ZoomByFactor not called


def test_flush_pan_sets_translation3():
    drv = SolidWorksDriver()
    model, view = _attach_fakes(drv)
    drv._mkvec = lambda x, y, z: FakeVector([x, y, z])      # bypass pywin32 VARIANT/CreateVector
    drv._flush((0, 0, 0, 1.0, -2.0, 0.0))                   # current Translation3 starts at (0,0,0)
    assert len(view.translation_sets) == 1                  # pan = set Translation3 (screen meters)
    newvec = view.translation_sets[-1]
    assert newvec.data[0] == pytest.approx(solidworks_driver.PAN_SIGN[0] * 1.0 * solidworks_driver.PAN_SCALE)
    assert newvec.data[1] == pytest.approx(solidworks_driver.PAN_SIGN[1] * -2.0 * solidworks_driver.PAN_SCALE)
    assert model.redraws == 1


def test_flush_no_active_doc_is_noop():
    drv = SolidWorksDriver()

    class NoDoc:
        ActiveDoc = None

    drv._swApp = NoDoc()
    drv._flush((0.1, 0.1, 0.1, 0.1, 0.1, 0.1))              # must not raise


def test_flush_one_failing_op_does_not_blank_the_rest():
    # Regression: a single failing camera op must NOT skip the others or the redraw (that turned
    # the whole viewport dead). Make orbit raise; pan, zoom, and the redraw must still run.
    drv = SolidWorksDriver()
    model, view = _attach_fakes(drv)
    drv._mkvec = lambda x, y, z: FakeVector([x, y, z])     # bypass pywin32 VARIANT/CreateVector
    view.orient_raises = True
    drv._flush((0.1, 0.0, 0.0, 1.0, 0.0, 0.5))             # orbit (fails) + pan + zoom
    assert view.rotations == []                            # orbit produced nothing
    assert len(view.translation_sets) == 1                 # pan still applied
    assert len(view.zooms) == 1                            # zoom still applied
    assert model.redraws == 1                              # viewport still redrawn
    assert "orbit" in drv._warned                          # failure logged (once)


# --- control scheme: set_scheme + pivot/box math + orbit/zoom honoring it -------------
def test_set_scheme_updates():
    drv = SolidWorksDriver()
    drv.set_scheme("object", "turntable", "to_object")
    assert drv._scheme == {"op": "object", "os": "turntable", "zm": "to_object",
                           "sel_override": True}


def test_compute_object_center_part_vs_assembly_vs_none():
    part = FakeModel(FakeView(), doc_type=1, box=(-1.0, -2.0, -3.0, 1.0, 4.0, 5.0))
    assert SolidWorksDriver._compute_object_center(part) == pytest.approx((0.0, 1.0, 1.0))   # GetPartBox
    asm = FakeModel(FakeView(), doc_type=2, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))
    assert SolidWorksDriver._compute_object_center(asm) == pytest.approx((1.0, 1.0, 1.0))     # GetBox
    assert SolidWorksDriver._compute_object_center(FakeModel(FakeView(), box=None)) is None


def test_set_pivot_hold_clamps():
    drv = SolidWorksDriver()
    drv.set_pivot_hold(2.0); assert drv._pivot_hold_sec == 2.0
    drv.set_pivot_hold(-5); assert drv._pivot_hold_sec == 0.0           # clamped up
    drv.set_pivot_hold(999); assert drv._pivot_hold_sec == 10.0         # clamped down
    drv.set_pivot_hold("x"); assert drv._pivot_hold_sec == solidworks_driver.DEFAULT_PIVOT_HOLD


def test_origin_orbit_is_pure_rotation_no_translation():
    # origin pivot (the original behaviour) rotates about the world origin with ZERO view translation.
    drv = SolidWorksDriver(); drv.set_scheme("origin", "free", "to_center")
    _model, view = _attach_fakes(drv, doc_type=1, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))
    drv._flush((0.1, 0.05, 0.0, 0.0, 0.0, 0.0))
    assert len(view.rotations) == 1
    assert view.rotations[0][1:4] == (0.0, 0.0, 0.0)        # about the model origin
    assert view.translation_sets == []                      # NO pan -> no translation


def test_object_orbit_pans_to_hold_centroid():
    # object pivot rotates about the model centroid -> rotation + a recenter pan (translation present).
    drv = SolidWorksDriver(); drv.set_scheme("object", "free", "to_center")
    _model, view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))   # centroid (1,1,1)
    drv._flush((0.1, 0.05, 0.0, 0.0, 0.0, 0.0))
    assert len(view.rotations) == 1
    assert len(view.translation_sets) == 1                  # pans to hold the off-origin centroid
    assert drv._orbit_pivot is None                         # object uses the centroid directly, not the hold


def test_view_orbit_rotates_and_pans_to_hold_pivot():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    _model, view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))   # centroid (1,1,1)
    drv._flush((0.1, 0.05, 0.0, 0.0, 0.0, 0.0))
    assert len(view.rotations) == 1
    assert len(view.translation_sets) == 1                  # view orbit pans to hold the screen centre


def test_set_scheme_releases_held_pivot():
    # Switching the pivot mid-use must take effect immediately, not stay stuck on the held pivot.
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    _model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot is not None
    drv.set_scheme("origin", "free", "to_center")
    assert drv._orbit_pivot is None


def test_flush_freezes_graphics_around_ops():
    # The whole camera change is wrapped in EnableGraphicsUpdate False->True so the rotate + recenter
    # pan show as ONE redraw (no flicker through the intermediate), then a single GraphicsRedraw2.
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    model, view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))
    drv._flush((0.05, 0.03, 0.0, 0.0, 0.0, 0.0))
    assert view.graphics_update_calls == [False, True]   # frozen for the ops, resumed before redraw
    assert model.redraws == 1                             # exactly one repaint, at the end


def test_pan_recomputes_view_pivot():
    # Panning moves the screen centre, so a held 'view' pivot must be dropped and recomputed.
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    _model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))            # orbit -> capture view pivot
    assert drv._orbit_pivot is not None
    drv._flush((0.0, 0.0, 0.0, 1.0, 0.0, 0.0))            # pan -> drop it
    assert drv._orbit_pivot is None


def test_view_pivot_held_within_threshold_recomputed_after():
    # Held while the view stays busy; recomputed once it has been idle past the threshold.
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    drv.set_pivot_hold(0.5)
    _model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    p1 = drv._orbit_pivot
    assert p1 is not None
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))             # immediate -> idle < 0.5 -> SAME pivot
    assert drv._orbit_pivot is p1
    drv._last_activity_t -= 10.0                            # simulate 10 s of stillness
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))             # idle >= 0.5 -> recompute (fresh object)
    assert drv._orbit_pivot is not p1


def test_view_pivot_is_screen_centre_at_object_depth():
    drv = SolidWorksDriver()
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0))   # centroid (1,1,1)
    drv._scale = 4.0; drv._trans = [0.0, 0.0, 0.0]
    # no pick handles set (not gone through _flush) -> no raycast -> object depth.
    # in-plane offset -T/Scale2 = 0, depth = col2.centroid = 1 -> (0,0,1)
    assert drv._view_pivot((1, 0, 0), (0, 1, 0), (0, 0, 1), model) == pytest.approx((0.0, 0.0, 1.0))


# --- 'view' pivot screen-centre raycast (SelectByRay) ---------------------------------
def test_view_pivot_uses_raycast_surface_depth():
    # The 'view' pivot's DEPTH comes from a screen-centre raycast, not the object centre. Identity
    # orientation -> c2=(0,0,1), screen-centre offset 0 -> pivot = (0,0,depth) = (0,0,hit.z).
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    _model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                  ray_hits=[(0.0, (1.0, 1.0, 1.5))])     # surface at depth 1.5
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 1.5))            # NOT the object centre (1.0)


def test_view_pivot_raycast_miss_falls_back_to_object_depth():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0), ray_hits=None)  # always miss
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 1.0))            # object-centre depth
    assert len(model._ext.ray_calls) == len(solidworks_driver._RAY_APERTURE_FRACS)  # grew through all


def test_view_pivot_raycast_rejects_out_of_bbox_hit():
    # A hit well outside the bbox (+margin) is bogus -> rejected -> fall back to object depth.
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    _model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                  ray_hits=[(0.0, (0.0, 0.0, 100.0))])
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 1.0))


def test_view_pivot_raycast_expands_aperture_until_hit():
    # Small apertures miss; the radius grows x3 until one hits, and the FIRST hit wins.
    box = (0.0, 0.0, 0.0, 2.0, 2.0, 2.0)
    diag = math.sqrt(12.0)
    thresh = solidworks_driver._RAY_APERTURE_FRACS[2] * diag - 1e-9    # only the 3rd radius is big enough
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    model, _view = _attach_fakes(drv, box=box, ray_hits=[(thresh, (1.0, 1.0, 1.5))])
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 1.5))
    assert len(model._ext.ray_calls) == 3                              # 2 misses then a hit -> stop


def test_view_pivot_raycast_picks_nearest_among_multiple_hits():
    # Several faces in a fat aperture -> take the one nearest the viewer (largest c2.hit).
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                 ray_hits=[(0.0, (1.0, 1.0, 1.2)), (0.0, (1.0, 1.0, 1.8))])
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 1.8))          # nearer surface, not 1.2
    assert len(model._ext.ray_calls) == 1                             # both hit at the first aperture


def test_view_pivot_raycast_passes_integer_tol():
    # Regression: SelectByRay's Tol arg must marshal as an INTEGER or it silently selects nothing.
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                 ray_hits=[(0.0, (1.0, 1.0, 1.5))])
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    tol = model._ext.ray_calls[0]["tol"]
    assert isinstance(tol, int) and not isinstance(tol, bool)


def test_raycast_saves_and_restores_user_selection():
    # SelectByRay clobbers the selection set; the user's selection must come back afterwards.
    drv = SolidWorksDriver(); drv.set_scheme(
        "view", "free", "to_center", selection_overrides_pivot=False)
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                 ray_hits=[(0.0, (1.0, 1.0, 1.5))])
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))            # prime _live_view (sets the pick handles)
    user_ent = FakeEntity((9.0, 9.0, 9.0), model._selmgr)
    model._selmgr._sel = [user_ent]                       # the user has something selected
    drv._last_activity_t -= 10.0                          # force a fresh pivot capture (raycast)
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert model._selmgr._sel == [user_ent]               # user's selection restored
    assert user_ent.select_appends == [True]              # re-selected via append (set, not marks)
    assert len(model._ext.ray_calls) >= 1                 # the raycast did run


def test_selection_override_wins_without_mutating_selection():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                 ray_hits=[(0.0, (1.0, 1.0, 1.5))])
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    selected = FakeEntity((0.25, 0.5, 0.75), model._selmgr)
    model._selmgr._sel = [selected]
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert model._selmgr._sel == [selected]
    assert model._ext.ray_calls == []                     # selection wins before view raycast
    assert len(_view.translation_sets) == 1               # off-origin selection point held


def test_selection_override_can_be_disabled():
    drv = SolidWorksDriver(); drv.set_scheme(
        "view", "free", "to_center", selection_overrides_pivot=False)
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                 ray_hits=[(0.0, (1.0, 1.0, 1.5))])
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    model._selmgr._sel = [FakeEntity((0.25, 0.5, 0.75), model._selmgr)]
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert len(model._ext.ray_calls) >= 1                 # designated view pivot restored


def test_object_orbit_does_not_raycast():
    drv = SolidWorksDriver(); drv.set_scheme("object", "free", "to_center")
    model, _view = _attach_fakes(drv, box=(0.0, 0.0, 0.0, 2.0, 2.0, 2.0),
                                 ray_hits=[(0.0, (1.0, 1.0, 1.5))])
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert model._ext.ray_calls == []                     # only the 'view' pivot raycasts


def test_flush_turntable_drops_roll():
    drv = SolidWorksDriver(); drv.set_scheme("view", "turntable", "to_center")
    _model, view = _attach_fakes(drv)
    drv._flush((0.0, 0.0, 0.5, 0.0, 0.0, 0.0))  # pure roll -> turntable has no roll channel
    assert view.rotations == []


def test_flush_turntable_yaw_is_about_world_up():
    drv = SolidWorksDriver(); drv.set_scheme("view", "turntable", "to_center")
    _model, view = _attach_fakes(drv)
    drv._flush((0.0, 0.3, 0.0, 0.0, 0.0, 0.0))  # pure yaw
    assert len(view.rotations) == 1
    r = view.rotations[0]
    # yaw axis is WORLD up (0,1,0), independent of the camera orientation
    assert (round(r[4], 6), round(abs(r[5]), 6), round(r[6], 6)) == (0.0, 1.0, 0.0)


def test_flush_zoom_to_object_zooms_and_recenters():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_object")
    _model, view = _attach_fakes(drv, box=(-0.5, -0.5, 0.0, 1.5, 1.5, 0.0))  # centre (0.5,0.5,0)
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.5))
    assert len(view.zooms) == 1                 # zoomed
    assert len(view.translation_sets) == 1      # AND recentred so the object centre stays put
    ds = 4.0 - 4.0 * view.zooms[0]              # Scale2 4.0 -> 4.0*factor; ds = before-after
    nv = view.translation_sets[-1]
    assert nv.data[0] == pytest.approx(ds * 0.5)   # col0.C = C[0] = 0.5 under identity orientation
    assert nv.data[1] == pytest.approx(ds * 0.5)


def test_flush_zoom_to_center_does_not_pan():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_center")
    _model, view = _attach_fakes(drv, box=(-0.5, -0.5, 0.0, 0.5, 0.5, 0.0))
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.5))
    assert len(view.zooms) == 1
    assert view.translation_sets == []          # to_center zoom never pans


# --- connection-state callback --------------------------------------------------------
def test_connection_callback_dedups():
    events = []
    drv = SolidWorksDriver(on_connection_changed=lambda c, v: events.append((c, v)))
    drv._set_connected(True)
    drv._set_connected(True)                                # duplicate suppressed
    drv._set_connected(False)
    drv._set_connected(False)
    assert events == [(True, ""), (False, "")]


# --- attach (the COM-instance lookup is monkeypatched: _find_running_sw) ---------------
def test_attach_success_sets_state_and_fires(monkeypatch):
    app = FakeApp(FakeModel(FakeView()))
    monkeypatch.setattr(SolidWorksDriver, "_find_running_sw", staticmethod(lambda: app))
    events = []
    drv = SolidWorksDriver(on_connection_changed=lambda c, v: events.append((c, v)))
    assert drv._attach() is True
    assert drv.is_connected() is True
    assert drv.version() == "32.3.0"
    assert events == [(True, "32.3.0")]


def test_attach_failure_when_sw_not_running(monkeypatch):
    monkeypatch.setattr(SolidWorksDriver, "_find_running_sw", staticmethod(lambda: None))
    drv = SolidWorksDriver()
    assert drv._attach() is False
    assert drv.is_connected() is False


def test_handle_drop_marks_disconnected(monkeypatch):
    monkeypatch.setattr(SolidWorksDriver, "_find_running_sw",
                        staticmethod(lambda: FakeApp(FakeModel(FakeView()))))
    events = []
    drv = SolidWorksDriver(on_connection_changed=lambda c, v: events.append((c, v)))
    drv._attach()
    drv._handle_drop()
    assert drv.is_connected() is False
    assert drv._swApp is None
    assert events[-1] == (False, "")


def test_start_without_pywin32_is_noop(monkeypatch):
    monkeypatch.setattr(solidworks_driver, "_PYWIN32", False)
    drv = SolidWorksDriver()
    drv.start()                                             # logs + returns; no thread
    assert drv._thread is None


# --- worker thread: real start/stop, flush, and accumulator zeroing -------------------
@requires_pywin32
def test_worker_thread_flushes_and_zeros(monkeypatch):
    drv = SolidWorksDriver(rate_hz=200)                     # ~5 ms period
    holder = {}

    def fake_attach():
        model = FakeModel(FakeView())
        drv._swApp = FakeApp(model)
        drv._mathUtil = FakeMathUtil()
        drv._set_connected(True)
        holder["model"] = model
        return True

    monkeypatch.setattr(drv, "_attach", fake_attach)
    drv.submit(0.2, 0.0, 0.0, 0.0, 0.0, 0.0)
    drv.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if holder.get("model") and holder["model"].redraws >= 1:
                break
            time.sleep(0.02)
    finally:
        drv.stop()
        if drv._thread:
            drv._thread.join(timeout=2.0)

    assert holder.get("model") is not None
    assert holder["model"].redraws >= 1                     # the worker applied a frame
    assert drv._acc == [0.0] * 6                            # accumulator drained after flush


# --- 'cursor' pivot: OS cursor -> Transform inverse -> (a,b) -> SelectByRay ---------------
# _cursor_client_point (the ctypes Win32 chain) is monkeypatched with a synthetic cursor px;
# everything downstream -- the IModelView.Transform inversion (verified live: a model->screen-
# PIXEL map whose column-rows are +c0 / -c1 and whose output space matches GetCursorPos) ->
# (a, b) -> ray -> held pivot -- is real code.
_BOX = (-0.1, -0.1, -0.1, 0.1, 0.1, 0.1)          # 0.2 m cube: diag ~0.3464, centre (0,0,0)

# identity camera (c0=X, c1=Y, c2=Z): px = 4000*(c0.P) + 760, 4000*(-c1.P) + 400 (y-down)
_XF_S, _XF_T = 4000.0, (760.0, 400.0)
_XF = (1.0, 0.0, 0.0,  0.0, -1.0, 0.0,  0.0, 0.0, -1.0,
       _XF_T[0], _XF_T[1], 0.0,  _XF_S,  0.0, 0.0, 0.0)


def _cursor_px(a, b):
    """The desktop pixel where a ray with in-plane offsets (a, b) sits under _XF."""
    return (_XF_S * a + _XF_T[0], -_XF_S * b + _XF_T[1])


def _cursor_setup(drv, ray_hits=None, a=0.00635, b=0.003175):
    model, view = _attach_fakes(drv, box=_BOX, ray_hits=ray_hits)
    view.Transform = FakeTransform(_XF)
    drv._cursor_client_point = lambda: _cursor_px(a, b)
    return model, view


def test_cursor_screen_ab_inverts_view_transform():
    drv = SolidWorksDriver()
    view = FakeView()
    view.Transform = FakeTransform(_XF)
    drv._cursor_client_point = lambda: _cursor_px(0.007, -0.016)
    a, b = drv._cursor_screen_ab(view, (1, 0, 0), (0, 1, 0))
    assert a == pytest.approx(0.007)
    assert b == pytest.approx(-0.016)              # the y-down flip is resolved per capture


def test_cursor_screen_ab_resolves_row_signs():
    # a transform whose in-plane rows are -c0 / +c1 (opposite handedness) still maps correctly
    drv = SolidWorksDriver()
    view = FakeView()
    view.Transform = FakeTransform((-1.0, 0.0, 0.0,  0.0, 1.0, 0.0,  0.0, 0.0, 1.0,
                                    _XF_T[0], _XF_T[1], 0.0,  _XF_S,  0.0, 0.0, 0.0))
    # px for (a,b) under THIS transform: u = s*(-a)+t0, v = s*(+b)+t1
    drv._cursor_client_point = lambda: (_XF_S * -0.007 + _XF_T[0], _XF_S * -0.016 + _XF_T[1])
    a, b = drv._cursor_screen_ab(view, (1, 0, 0), (0, 1, 0))
    assert a == pytest.approx(0.007)
    assert b == pytest.approx(-0.016)


def test_cursor_screen_ab_rejects_misaligned_transform():
    # Transform rows not aligned with the camera axes (model changed / stale read) -> None,
    # never a silently-wrong pivot.
    drv = SolidWorksDriver()
    view = FakeView()
    s2 = math.sqrt(0.5)
    view.Transform = FakeTransform((s2, -s2, 0.0,  s2, s2, 0.0,  0.0, 0.0, 1.0,
                                    _XF_T[0], _XF_T[1], 0.0,  _XF_S,  0.0, 0.0, 0.0))
    drv._cursor_client_point = lambda: (800.0, 380.0)
    assert drv._cursor_screen_ab(view, (1, 0, 0), (0, 1, 0)) is None


def test_cursor_screen_ab_none_without_cursor():
    drv = SolidWorksDriver()
    view = FakeView()
    view.Transform = FakeTransform(_XF)
    drv._cursor_client_point = lambda: None       # not over the view window / Win32 failure
    assert drv._cursor_screen_ab(view, (1, 0, 0), (0, 1, 0)) is None


def test_cursor_orbit_raycasts_through_cursor_pixel():
    drv = SolidWorksDriver(); drv.set_scheme("cursor", "free", "to_center")
    a, b = 0.00635, 0.003175
    _model, view = _cursor_setup(drv, ray_hits=[(0.0, (0.006, 0.003, 0.1))], a=a, b=b)
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    call = _model._ext.ray_calls[0]
    assert call["dir"] == pytest.approx((0.0, 0.0, -1.0))    # into the screen (-c2)
    diag = math.sqrt(3 * 0.2 ** 2)
    push = solidworks_driver._RAY_PUSH * diag                # anchored at obj depth 0, pushed back
    assert call["origin"] == pytest.approx((a, b, push))
    # pivot = a*c0 + b*c1 + depth*c2 with depth = c2.hit = 0.1
    assert drv._orbit_pivot == pytest.approx((a, b, 0.1))
    assert len(view.rotations) == 1                          # and the orbit still happened


def test_cursor_orbit_holds_pivot_within_threshold():
    drv = SolidWorksDriver(); drv.set_scheme("cursor", "free", "to_center")
    drv.set_pivot_hold(0.5)
    _model, _view = _cursor_setup(drv, ray_hits=[(0.0, (0.006, 0.003, 0.1))])
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    p1 = drv._orbit_pivot
    n_calls = len(_model._ext.ray_calls)
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))             # immediate -> held, NO new raycast
    assert drv._orbit_pivot is p1
    assert len(_model._ext.ray_calls) == n_calls
    drv._last_activity_t -= 10.0                            # gesture over -> re-raycast
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert len(_model._ext.ray_calls) > n_calls


def test_cursor_orbit_miss_falls_back_to_object_centre():
    drv = SolidWorksDriver(); drv.set_scheme("cursor", "free", "to_center")
    _model, _view = _cursor_setup(drv, ray_hits=None)        # nothing under the cursor
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 0.0))   # the box centre, held


def test_cursor_orbit_unmappable_cursor_falls_back_without_raycast():
    drv = SolidWorksDriver(); drv.set_scheme("cursor", "free", "to_center")
    _model, _view = _cursor_setup(drv, ray_hits=[(0.0, (0.0, 0.0, 0.1))])
    drv._cursor_client_point = lambda: None                 # over another window / Win32 failure
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert _model._ext.ray_calls == []                       # never raycast a bogus mapping
    assert drv._orbit_pivot == pytest.approx((0.0, 0.0, 0.0))


def test_zoom_to_cursor_holds_cursor_point():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_cursor")
    a, b = 0.00635, 0.003175
    _model, view = _cursor_setup(drv, ray_hits=[(0.0, (0.006, 0.003, 0.1))], a=a, b=b)
    drv._flush((0, 0, 0, 0, 0, 0.5))
    factor = 1.0 + solidworks_driver.ZOOM_SIGN * 0.5 * solidworks_driver.ZOOM_SCALE
    assert view.zooms == [pytest.approx(factor)]
    # recenter pan holds the cursor point P: dT = (s_before - s_after) * (col.P)
    ds = 4.0 - 4.0 * factor
    assert view.translation_sets, "to_cursor zoom must recenter the held point"
    assert tuple(view.translation_sets[-1].data) == pytest.approx((ds * a, ds * b, 0.0))
    # a second zoom within the hold window reuses the pivot (no new raycast)
    n_calls = len(_model._ext.ray_calls)
    drv._flush((0, 0, 0, 0, 0, 0.2))
    assert len(_model._ext.ray_calls) == n_calls


def test_zoom_to_cursor_miss_is_plain_center_zoom():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_cursor")
    _model, view = _cursor_setup(drv, ray_hits=None)
    drv._flush((0, 0, 0, 0, 0, 0.5))
    assert len(view.zooms) == 1
    assert view.translation_sets == []                       # no recenter -> native centre zoom


def test_orbit_and_pan_reset_zoom_cursor_pivot():
    drv = SolidWorksDriver(); drv.set_scheme("view", "free", "to_cursor")
    _model, _view = _cursor_setup(drv)
    drv._zoom_pivot = (1.0, 2.0, 3.0)
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))             # orbit -> reset
    assert drv._zoom_pivot is None
    drv._zoom_pivot = (1.0, 2.0, 3.0)
    drv._flush((0.0, 0.0, 0.0, 0.3, 0.0, 0.0))              # pan -> reset
    assert drv._zoom_pivot is None


def test_set_scheme_releases_zoom_pivot():
    drv = SolidWorksDriver()
    drv._zoom_pivot = (1.0, 2.0, 3.0)
    drv.set_scheme("view", "free", "to_center")
    assert drv._zoom_pivot is None
