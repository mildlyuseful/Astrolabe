# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the AutoCAD COM driver -- everything except the live COM boundary.

The COM layer is mocked: _flush() runs against a fake AutoCAD.Application / Document / Viewport, and
attach runs against a monkeypatched _find_running_acad. No AutoCAD is required. What still needs a
live AutoCAD: the real attach and that orbit/pan/zoom directions + sensitivities feel right (see
docs/apps/autocad.md and the module's tuning constants).

Key verified facts these fakes encode: the driver READS the view from sysvars (VIEWDIR/TARGET/
VIEWSIZE/EXTMIN/EXTMAX), never from the viewport clone; it WRITES via the reassign commit ritual
(set Direction/Target/Height/Center, then `doc.ActiveViewport = vp`, then `acad.Update()`); Direction/
Target/Center are set as SAFEARRAY VARIANTs; zoom uses acad.ZoomScaled/ZoomCenter.
"""
import math
import time

import pytest

from trackball_daemon import autocad_driver
from trackball_daemon.autocad_driver import AutoCADDriver

requires_pywin32 = pytest.mark.skipif(
    not autocad_driver._PYWIN32, reason="pywin32 not installed")


@pytest.fixture(autouse=True)
def _overlay_off(monkeypatch):
    """The deferred-orbit overlay creates a real Win32 window; keep it out of unit tests. The
    overlay-specific tests flip this back on and inject a FakeOverlay instead."""
    monkeypatch.setattr(autocad_driver, "ORBIT_OVERLAY", False)


def _val(v):
    """Unwrap a SAFEARRAY VARIANT (pywin32) to a plain list, or pass a plain list/tuple through."""
    return list(getattr(v, "value", v))


# --- fakes mimicking the AutoCAD COM objects the driver touches ------------------------
class FakeViewport:
    """Records the view properties the driver sets (Direction/Target/Height/Center). The driver never
    READS these (it reads the live view from sysvars), so getters just return the last set value.
    Set direction_raises=True to simulate a failing commit."""

    def __init__(self):
        self.direction = None
        self.target = None
        self.height = None
        self.center = None
        self.direction_raises = False

    @property
    def Direction(self):
        return self.direction

    @Direction.setter
    def Direction(self, v):
        if self.direction_raises:
            raise RuntimeError("Direction set boom")
        self.direction = _val(v)

    @property
    def Target(self):
        return self.target

    @Target.setter
    def Target(self, v):
        self.target = _val(v)

    @property
    def Height(self):
        return self.height

    @Height.setter
    def Height(self, v):
        self.height = float(v)

    @property
    def Center(self):
        return self.center

    @Center.setter
    def Center(self, v):
        self.center = _val(v)


class FakeDoc:
    """Stands in for AcadDocument. Serves the sysvars the driver reads (VIEWDIR/TARGET/VIEWCTR/VIEWSIZE/
    EXTMIN/EXTMAX), exposes ActiveSpace, and records the ActiveViewport reassign (the commit).
    VIEWCTR defaults to TARGET (the driver seeds the pivot from VIEWCTR, the actually-centred point)."""

    def __init__(self, viewdir=(-1.0, -1.0, 1.0), target=(0.0, 0.0, 0.0), viewsize=10.0,
                 extmin=(-5.0, -3.0, -2.0), extmax=(5.0, 3.0, 2.0), active_space=1, box=True,
                 viewctr=None):
        self._view = FakeViewport()
        self._vars = {"VIEWDIR": viewdir, "TARGET": target, "VIEWSIZE": viewsize,
                      "VIEWCTR": viewctr if viewctr is not None else target}
        if box:
            self._vars["EXTMIN"] = extmin
            self._vars["EXTMAX"] = extmax
        self.ActiveSpace = active_space
        self.reassigns = []                 # viewports assigned back (the commit)

    def GetVariable(self, name):
        if name not in self._vars:
            raise RuntimeError("unknown sysvar %r" % name)
        return self._vars[name]

    @property
    def ActiveViewport(self):
        return self._view

    @ActiveViewport.setter
    def ActiveViewport(self, vp):
        self.reassigns.append(vp)


class FakeDocuments:
    def __init__(self, count=1):
        self.Count = count


class FakeApp:
    """Stands in for AcadApplication."""

    def __init__(self, doc=None, count=1, name="AutoCAD"):
        self._doc = doc if doc is not None else FakeDoc()
        self.Documents = FakeDocuments(count)
        self.Version = "25.1s (LMS Tech)"
        self.Name = name
        self.HWND = 4242                    # AcadApplication.HWND (the overlay centres on it)
        self.updates = 0
        self.zoom_scaled = []               # (factor, enum)
        self.zoom_center = []               # (center_list, magnitude)

    @property
    def ActiveDocument(self):
        return self._doc

    def Update(self):
        self.updates += 1

    def ZoomScaled(self, factor, enum):
        self.zoom_scaled.append((float(factor), enum))

    def ZoomCenter(self, center, magnification):
        self.zoom_center.append((_val(center), float(magnification)))


def _attach_fakes(drv, **doc_kw):
    doc = FakeDoc(**doc_kw)
    acad = FakeApp(doc)
    drv._acad = acad
    return acad, doc, doc._view


def _orbit_now(drv, ox, oy, oz):
    """Apply an orbit through the REAL deferred path: accumulate via _flush, then simulate the gesture
    going idle so _flush_idle applies the pending orbit (one reassign)."""
    drv._flush((ox, oy, oz, 0.0, 0.0, 0.0))
    drv._orbit_last_t -= 10.0                    # pretend the gesture stopped a while ago
    drv._flush_idle()


# --- submit / accumulate --------------------------------------------------------------
def test_submit_accumulates():
    drv = AutoCADDriver()
    drv.submit(1, 2, 3, 4, 5, 6)
    drv.submit(0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    assert drv._acc == [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]


# --- rate clamp / set_rate ------------------------------------------------------------
def test_clamp_rate_bounds_and_fallback():
    assert AutoCADDriver._clamp_rate(0) == 1.0
    assert AutoCADDriver._clamp_rate(10_000) == 240.0
    assert AutoCADDriver._clamp_rate(30) == 30.0
    assert AutoCADDriver._clamp_rate("nope") == autocad_driver.DEFAULT_FLUSH_HZ


def test_set_rate_updates_period():
    drv = AutoCADDriver()
    drv.set_rate(60)
    assert drv._period == pytest.approx(1.0 / 60.0)
    drv.set_rate(0)
    assert drv._period == pytest.approx(1.0)


def test_set_scheme_updates():
    drv = AutoCADDriver()
    drv.set_scheme("object", "turntable", "to_object")
    assert drv._scheme == {"op": "object", "os": "turntable", "zm": "to_object"}


# --- orbit application mode: deferred (default, + overlay) vs per-frame (opt-out) -----
def test_orbit_mode_defaults_to_deferred():
    # Deferred is the shipping default: one regen per gesture, with the overlay as live feedback.
    assert autocad_driver._ORBIT_DEFER is True


def test_orbit_immediate_mode_applies_every_frame(monkeypatch):
    # _ORBIT_DEFER False: orbit applies EVERY frame -> continuous movement (one reassign per flush).
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", False)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, doc, _vp = _attach_fakes(drv)
    drv._flush((0.1, 0.05, 0.0, 0.0, 0.0, 0.0))
    assert len(doc.reassigns) == 1                               # applied immediately (per-frame)
    assert drv._pending_orbit == [0.0, 0.0, 0.0]                 # nothing left pending


def test_orbit_deferred_accumulates_until_idle(monkeypatch):
    # Opt-in defer mode: an orbit frame must NOT reassign immediately -- it accumulates, then applies
    # once the gesture pauses (idle >= _ORBIT_IDLE) as ONE reassign.
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", True)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, doc, _vp = _attach_fakes(drv)
    drv._flush((0.1, 0.05, 0.0, 0.0, 0.0, 0.0))
    assert doc.reassigns == []                                   # NOT applied yet (deferred)
    assert drv._pending_orbit != [0.0, 0.0, 0.0] and drv._orbit_since is not None
    drv._flush_idle()                                            # not idle long enough yet
    assert doc.reassigns == []
    drv._orbit_last_t -= 10.0                                    # now the gesture has "stopped"
    drv._flush_idle()
    assert len(doc.reassigns) == 1                               # applied once
    assert drv._pending_orbit == [0.0, 0.0, 0.0] and drv._orbit_since is None


def test_orbit_deferred_max_defer_applies_during_continuous_roll(monkeypatch):
    # Defer mode: during a sustained roll (never idle), the max-defer cap still applies periodically.
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", True)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, doc, _vp = _attach_fakes(drv)
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))                  # starts the batch (deferred)
    assert doc.reassigns == []
    drv._orbit_since -= (autocad_driver._ORBIT_MAX_DEFER + 1.0)  # batch has run past the cap
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))                  # next frame -> forced apply
    assert len(doc.reassigns) == 1


# --- defer-mode overlay (the rotating-cube feedback; a fake stands in for the Win32 window) -----
class FakeOverlay:
    def __init__(self):
        self.shows = []
        self.updates = []
        self.hides = 0

    def show(self, hwnd):
        self.shows.append(hwnd)

    def update(self, view_dir):
        self.updates.append(tuple(view_dir))

    def hide(self):
        self.hides += 1

    def destroy(self):
        pass


def test_overlay_previews_pending_orbit_and_hides_on_apply(monkeypatch):
    # Defer mode + overlay: each accumulating frame refreshes the cube with the direction the view
    # WILL have; when the gesture pauses and the orbit lands, the overlay hides.
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", True)
    monkeypatch.setattr(autocad_driver, "ORBIT_OVERLAY", True)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, doc, _vp = _attach_fakes(drv)
    fake = FakeOverlay(); drv._overlay = fake
    drv._flush((0.1, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert fake.shows == [4242]                      # centred on acad.HWND
    assert len(fake.updates) == 1
    first = fake.updates[-1]
    drv._flush((0.1, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert len(fake.updates) == 2
    assert fake.updates[-1] != first                 # cube keeps rotating with the accumulated orbit
    assert doc.reassigns == [] and fake.hides == 0   # real view untouched, overlay up
    drv._orbit_last_t -= 10.0
    drv._flush_idle()                                # gesture over -> apply once + hide the overlay
    assert len(doc.reassigns) == 1
    assert fake.hides == 1


def test_overlay_stays_up_across_max_defer_apply(monkeypatch):
    # A mid-gesture (max-defer cap) apply must NOT hide the overlay -- the gesture is still going.
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", True)
    monkeypatch.setattr(autocad_driver, "ORBIT_OVERLAY", True)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, doc, _vp = _attach_fakes(drv)
    fake = FakeOverlay(); drv._overlay = fake
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))
    drv._orbit_since -= (autocad_driver._ORBIT_MAX_DEFER + 1.0)
    drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))      # cap hit -> mid-gesture apply
    assert len(doc.reassigns) == 1
    assert fake.hides == 0                           # overlay stays up mid-gesture


def test_overlay_failure_disables_overlay_not_driving(monkeypatch):
    # One overlay exception disables the overlay (logged once) but never the actual driving.
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", True)
    monkeypatch.setattr(autocad_driver, "ORBIT_OVERLAY", True)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, doc, _vp = _attach_fakes(drv)

    class BoomOverlay(FakeOverlay):
        def update(self, view_dir):
            raise RuntimeError("gdi boom")

    drv._overlay = BoomOverlay()
    drv._flush((0.1, 0.0, 0.0, 0.0, 0.0, 0.0))       # overlay fails -> disabled, no raise
    assert drv._overlay_dead is True
    assert "overlay" in drv._warned
    drv._orbit_last_t -= 10.0
    drv._flush_idle()                                # the orbit still lands
    assert len(doc.reassigns) == 1


# --- orbit application (via the deferred path) ----------------------------------------
def test_orbit_commits_via_reassign_then_zoomcenter():
    # Applying the pending orbit: set Direction+Target+Height, reassign (the commit), then ZoomCenter to
    # re-centre. Height is set (kills the default-zoom flash); Center is NEVER set (destructive); no Update.
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, doc, vp = _attach_fakes(drv, viewsize=10.0)
    _orbit_now(drv, 0.1, 0.05, 0.0)
    assert len(doc.reassigns) == 1 and doc.reassigns[0] is vp   # committed by reassignment
    assert vp.direction is not None                             # new direction set
    assert vp.height == pytest.approx(10.0)                     # zoom set in the reassign (no flash)
    assert vp.center is None                                    # Center NEVER set (destructive)
    assert len(acad.zoom_center) == 1                           # re-centred via ZoomCenter
    _c, mag = acad.zoom_center[0]
    assert mag == pytest.approx(10.0)                           # VIEWSIZE preserved
    assert acad.updates == 0                                    # no redundant Update()


def test_orbit_free_rotates_direction():
    # Pure pitch (ox) about camera-right changes the view direction (a unit vector).
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, viewdir=(0.0, 0.0, 1.0))   # top view
    _orbit_now(drv, 0.2, 0.0, 0.0)
    d = vp.direction
    assert d != [0.0, 0.0, 1.0]                      # direction actually rotated
    assert math.sqrt(sum(c * c for c in d)) == pytest.approx(1.0)   # kept unit-length


def test_orbit_turntable_yaw_preserves_elevation():
    # Turntable yaw is about WORLD up (0,0,1): the Z component of the (normalized) view direction is
    # unchanged (verticals stay vertical), while X/Y rotate.
    drv = AutoCADDriver(); drv.set_scheme("view", "turntable", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, viewdir=(-1.0, -1.0, 1.0))
    z_before = 1.0 / math.sqrt(3.0)
    _orbit_now(drv, 0.0, 0.3, 0.0)                   # pure yaw
    assert vp.direction[2] == pytest.approx(z_before)          # elevation preserved
    assert (vp.direction[0], vp.direction[1]) != pytest.approx((-z_before, -z_before))  # azimuth moved


def test_orbit_turntable_drops_roll():
    # Turntable has no roll channel: a pure-roll (oz) gesture produces no rotation (direction unchanged).
    drv = AutoCADDriver(); drv.set_scheme("view", "turntable", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, viewdir=(-1.0, -1.0, 1.0))
    _orbit_now(drv, 0.0, 0.0, 0.5)
    n = 1.0 / math.sqrt(3.0)
    assert vp.direction == pytest.approx([-n, -n, n])          # unchanged (roll dropped)


def test_flush_pan_uses_zoomcenter_no_reassign():
    # Pan moves the screen centre via ZoomCenter (a smooth zoom -- NO reassign, so no regen).
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, doc, _vp = _attach_fakes(drv, viewdir=(0.0, 0.0, 1.0), target=(0.0, 0.0, 0.0), viewsize=10.0)
    drv._flush((0.0, 0.0, 0.0, 1.0, 0.0, 0.0))       # pan +X (top view: right = +X)
    assert doc.reassigns == []                        # pan must NOT reassign the viewport (no regen)
    assert len(acad.zoom_center) == 1
    center, mag = acad.zoom_center[0]
    # right=(1,0,0); dx = PAN_SIGN[0]*1*PAN_SCALE*VIEWSIZE ; centre starts at VIEWCTR (0,0,0)
    expected_dx = autocad_driver.PAN_SIGN[0] * 1.0 * autocad_driver.PAN_SCALE * 10.0
    assert center[0] == pytest.approx(expected_dx)
    assert center[1] == pytest.approx(0.0)
    assert mag == pytest.approx(10.0)                 # zoom unchanged
    assert acad.updates == 0


def test_pan_flushes_pending_orbit_first(monkeypatch):
    # In defer mode, if an orbit is pending when a pan arrives, the pan flushes the orbit first (one
    # reassign) so the view is consistent, then pans (ZoomCenter). Keeps deferred orbit + immediate pan
    # composing. (In the default per-frame mode there is never a pending orbit -- it applies at once.)
    monkeypatch.setattr(autocad_driver, "_ORBIT_DEFER", True)
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, doc, _vp = _attach_fakes(drv, viewsize=10.0)
    drv._flush((0.1, 0.0, 0.0, 0.0, 0.0, 0.0))       # orbit -> pending (no reassign yet)
    assert doc.reassigns == []
    drv._flush((0.0, 0.0, 0.0, 1.0, 0.0, 0.0))       # pan -> flush pending orbit, then pan
    assert len(doc.reassigns) == 1                    # the pending orbit was applied
    assert len(acad.zoom_center) == 2                 # orbit's ZoomCenter + pan's ZoomCenter
    assert drv._orbit_since is None                   # pending cleared


# --- zoom -----------------------------------------------------------------------------
def test_flush_zoom_to_center_uses_zoomscaled():
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, _doc, _vp = _attach_fakes(drv)
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.5))
    expected = 1.0 + autocad_driver.ZOOM_SIGN * 0.5 * autocad_driver.ZOOM_SCALE
    assert len(acad.zoom_scaled) == 1
    factor, enum = acad.zoom_scaled[0]
    assert factor == pytest.approx(expected)
    assert enum == autocad_driver.AC_ZOOM_SCALED_RELATIVE     # acZoomScaledRelative = 1 (verified)
    assert acad.zoom_center == []                     # to_center never uses ZoomCenter
    assert acad.updates == 0                          # zoom repaints itself; no redundant Update()


def test_flush_zoom_to_object_uses_zoomcenter_at_bbox():
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_object")
    acad, _doc, _vp = _attach_fakes(drv, extmin=(0.0, 0.0, 0.0), extmax=(2.0, 4.0, 6.0), viewsize=10.0)
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.5))
    assert len(acad.zoom_center) == 1
    center, mag = acad.zoom_center[0]
    assert center == pytest.approx([1.0, 2.0, 3.0])   # drawing-extents centre
    factor = 1.0 + autocad_driver.ZOOM_SIGN * 0.5 * autocad_driver.ZOOM_SCALE
    assert mag == pytest.approx(10.0 / factor)        # magnification = VIEWSIZE / factor (zoom in)
    assert acad.zoom_scaled == []


def test_flush_zoom_to_object_without_extents_falls_back_to_center():
    # No drawing extents -> to_object can't find a centre -> fall back to ZoomScaled (to_center).
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_object")
    acad, _doc, _vp = _attach_fakes(drv, box=False)
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, 0.5))
    assert len(acad.zoom_scaled) == 1
    assert acad.zoom_center == []


def test_flush_zoom_guards_nonpositive_factor():
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, _doc, _vp = _attach_fakes(drv)
    drv._flush((0.0, 0.0, 0.0, 0.0, 0.0, -1000.0))    # factor would be <= 0
    assert acad.zoom_scaled == [] and acad.zoom_center == []


# --- orbit pivots ---------------------------------------------------------------------
def test_pivot_origin_targets_world_origin():
    drv = AutoCADDriver(); drv.set_scheme("origin", "free", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, target=(7.0, 8.0, 9.0))
    _orbit_now(drv, 0.1, 0.0, 0.0)
    assert vp.target == pytest.approx([0.0, 0.0, 0.0])         # orbit about the WCS origin


def test_pivot_object_targets_extents_center():
    drv = AutoCADDriver(); drv.set_scheme("object", "free", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, target=(7.0, 8.0, 9.0),
                                    extmin=(0.0, 0.0, 0.0), extmax=(2.0, 2.0, 2.0))
    _orbit_now(drv, 0.1, 0.0, 0.0)
    assert vp.target == pytest.approx([1.0, 1.0, 1.0])         # drawing-extents centre


def test_pivot_view_targets_current_center():
    # 'view' orbits about the tracked centre (seeded from VIEWCTR -- here == TARGET in the fake).
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, target=(7.0, 8.0, 9.0))   # VIEWCTR defaults to TARGET
    _orbit_now(drv, 0.1, 0.0, 0.0)
    assert vp.target == pytest.approx([7.0, 8.0, 9.0])         # the currently-centred point


def test_pivot_cursor_falls_back_to_object():
    drv = AutoCADDriver(); drv.set_scheme("cursor", "free", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, target=(7.0, 8.0, 9.0),
                                    extmin=(0.0, 0.0, 0.0), extmax=(4.0, 4.0, 4.0))
    _orbit_now(drv, 0.1, 0.0, 0.0)
    assert vp.target == pytest.approx([2.0, 2.0, 2.0])         # cursor -> object (extents centre)


# --- guards & robustness --------------------------------------------------------------
def test_flush_paper_space_is_noop():
    # Only model space is driven (ActiveSpace == acModelSpace == 1); paper space / 2D -> no-op.
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, doc, _vp = _attach_fakes(drv, active_space=0)
    drv._flush((0.1, 0.1, 0.0, 0.0, 0.0, 0.0))
    assert doc.reassigns == [] and acad.updates == 0


def test_flush_no_document_is_noop():
    # AutoCAD running but no drawing open (Start tab) -> idle, NOT a disconnect.
    drv = AutoCADDriver()
    acad = FakeApp(count=0)
    drv._acad = acad
    drv._flush((0.1, 0.1, 0.1, 0.1, 0.1, 0.1))       # must not raise
    assert acad.updates == 0


def test_flush_one_failing_op_does_not_blank_the_rest():
    # A single failing op (here the orbit's Direction set) is logged once and skipped; a zoom in the
    # same coalesced frame still applies -- one failing COM call must never dead-screen the viewport.
    drv = AutoCADDriver(); drv.set_scheme("view", "free", "to_center")
    acad, doc, vp = _attach_fakes(drv)
    vp.direction_raises = True
    drv._flush((0.1, 0.0, 0.0, 0.0, 0.0, 0.5))       # orbit (Direction set fails) + zoom
    assert doc.reassigns == []                        # orbit reached no commit
    assert len(acad.zoom_scaled) == 1                 # zoom still applied
    assert "orbit" in drv._warned                     # failure logged (once)


def test_flush_tracks_view_state_across_writes():
    # After an orbit the driver tracks the new direction + pivot so it needn't re-read every frame.
    drv = AutoCADDriver(); drv.set_scheme("origin", "free", "to_center")
    _acad, _doc, vp = _attach_fakes(drv, target=(5.0, 0.0, 0.0))
    _orbit_now(drv, 0.1, 0.0, 0.0)
    assert drv._center == pytest.approx((0.0, 0.0, 0.0))       # tracked = the committed pivot (origin)
    assert list(drv._dir) == pytest.approx(vp.direction)      # tracked = the committed direction


# --- connection-state callback --------------------------------------------------------
def test_connection_callback_dedups():
    events = []
    drv = AutoCADDriver(on_connection_changed=lambda c, v: events.append((c, v)))
    drv._set_connected(True)
    drv._set_connected(True)
    drv._set_connected(False)
    drv._set_connected(False)
    assert events == [(True, ""), (False, "")]


# --- attach (the COM-instance lookup is monkeypatched: _find_running_acad) -------------
def test_attach_success_sets_state_and_fires(monkeypatch):
    acad = FakeApp()
    monkeypatch.setattr(AutoCADDriver, "_find_running_acad", staticmethod(lambda: acad))
    events = []
    drv = AutoCADDriver(on_connection_changed=lambda c, v: events.append((c, v)))
    assert drv._attach() is True
    assert drv.is_connected() is True
    assert drv.version() == "25.1s (LMS Tech)"
    assert events == [(True, "25.1s (LMS Tech)")]


def test_attach_failure_when_autocad_not_running(monkeypatch):
    monkeypatch.setattr(AutoCADDriver, "_find_running_acad", staticmethod(lambda: None))
    drv = AutoCADDriver()
    assert drv._attach() is False
    assert drv.is_connected() is False


def test_handle_drop_marks_disconnected(monkeypatch):
    monkeypatch.setattr(AutoCADDriver, "_find_running_acad", staticmethod(lambda: FakeApp()))
    events = []
    drv = AutoCADDriver(on_connection_changed=lambda c, v: events.append((c, v)))
    drv._attach()
    drv._handle_drop()
    assert drv.is_connected() is False
    assert drv._acad is None
    assert events[-1] == (False, "")


def test_start_without_pywin32_is_noop(monkeypatch):
    monkeypatch.setattr(autocad_driver, "_PYWIN32", False)
    drv = AutoCADDriver()
    drv.start()                                        # logs + returns; no thread
    assert drv._thread is None


# --- worker thread: real start/stop, flush, and accumulator zeroing -------------------
@requires_pywin32
def test_worker_thread_flushes_and_zeros(monkeypatch):
    drv = AutoCADDriver(rate_hz=200)
    holder = {}

    def fake_attach():
        acad = FakeApp()
        drv._acad = acad
        drv._set_connected(True)
        holder["acad"] = acad
        return True

    monkeypatch.setattr(drv, "_attach", fake_attach)
    drv.submit(0.2, 0.0, 0.0, 0.0, 0.0, 0.0)           # orbit -> reassign + ZoomCenter
    drv.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if holder.get("acad") and holder["acad"].zoom_center:
                break
            time.sleep(0.02)
    finally:
        drv.stop()
        if drv._thread:
            drv._thread.join(timeout=2.0)

    assert holder.get("acad") is not None
    assert holder["acad"].zoom_center                   # the worker applied a frame (orbit -> ZoomCenter)
    assert drv._acc == [0.0] * 6                        # accumulator drained after flush
