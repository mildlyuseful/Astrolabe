"""Headless unit tests for the Onshape bridge's under-cursor orbit pivot (Section 6).

The bridge drives Onshape in a browser, so the cursor->canvas mapping and the actual navlib
hit-test can only be verified with a live Onshape document (FLAGGED needs-live-GUI-verify in the
code + docs). These tests cover the parts that ARE deterministic offline, with a fake connection
that records the ray writes and returns a scripted hit:

  * _pixel_ray         -- the NDC canvas point -> world pick ray (centre reproduces the old ray;
                          an off-centre point offsets laterally by ndc*half-extent along right/up).
  * _client_fraction_to_ndc -- client-area fraction -> canvas NDC, with the y-flip, the inset
                          remap, and rejecting a cursor that falls outside the canvas.
  * _hit_cursor        -- end to end: cursor fraction -> ndc -> ray -> hit, and None off-canvas.
  * _pivot("cursor")   -- uses the cursor hit, falls back to the screen centre on a miss, then to
                          the model centre; and _pivot("view") still uses ONLY the screen centre.

What it CANNOT prove (needs the live browser): that the client-area fraction + CANVAS_INSET land on
the real WebGL canvas, and that Onshape's hit-test returns the surface under the mouse.
"""
import math
import os
import sys

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from trackball_daemon import onshape_bridge as ob  # noqa: E402
from trackball_daemon.onshape_bridge import OnshapeBridge, _PropUnsupported  # noqa: E402


# --- a fake WAMP connection: records hit.* writes, returns a scripted hit.lookat ----------------
class FakeConn:
    def __init__(self, hit=None, view_ext=None, model_ext=None, misses=0):
        self._hit_unsupported = False
        self._hit = hit                                   # hit.lookat to return, or None -> miss
        self._view_ext = view_ext if view_ext is not None else [-4.0, -3.0, -1.0, 4.0, 3.0, 1.0]
        self._model_ext = model_ext if model_ext is not None else [-10.0] * 3 + [10.0] * 3
        self.misses = misses                              # leading apertures that "miss" first
        self.writes = []

    def read(self, prop, ttl=0.0):
        if prop == "view.extents":
            return self._view_ext
        if prop == "model.extents":
            return self._model_ext
        return None

    def write(self, prop, value):
        self.writes.append((prop, value))

    def write_best_effort(self, prop, value):
        self.writes.append((prop, value))

    def _rpc(self, method, args):
        assert method == "self:read" and args == ["hit.lookat"]
        if self.misses > 0:
            self.misses -= 1
            raise _PropUnsupported(("", "no hit"))
        if self._hit is None:
            raise _PropUnsupported(("", "no hit"))
        return list(self._hit)

    def write_of(self, prop):
        for p, v in self.writes:
            if p == prop:
                return v
        return None


@pytest.fixture
def bridge():
    return OnshapeBridge()


def vclose(u, v, eps=1e-9):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


# --- _pixel_ray: centre reproduces the old ray; off-centre offsets by ndc*half along right/up ---
def test_pixel_ray_centre_matches_screen_centre():
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    lookfrom, direction = OnshapeBridge._pixel_ray(0.0, 0.0, eye, right, up, back, 4.0, 3.0, 40.0)
    assert vclose(direction, (0.0, 0.0, -1.0))            # forward = -back, normalized
    assert vclose(lookfrom, (0.0, 0.0, 90.0))             # eye - fwd*backoff = (0,0,50+40)


def test_pixel_ray_offcentre_offsets_along_right_up():
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    # ndc (0.5, 0.5): +0.5*half_x right, +0.5*half_y up
    lookfrom, direction = OnshapeBridge._pixel_ray(0.5, 0.5, eye, right, up, back, 4.0, 3.0, 40.0)
    assert vclose(direction, (0.0, 0.0, -1.0))            # ortho: parallel to the view axis
    assert vclose(lookfrom, (2.0, 1.5, 90.0))             # (0.5*4, 0.5*3, 50+40)


def test_pixel_ray_uses_the_camera_basis():
    # a rotated basis: right=+y, up=-x -> the lateral offset follows the basis, not world axes
    eye, right, up, back = (10.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)
    lookfrom, direction = OnshapeBridge._pixel_ray(1.0, 0.0, eye, right, up, back, 2.0, 2.0, 5.0)
    assert vclose(direction, (1.0, 0.0, 0.0))             # forward = -back = +x
    # offset = right*(1*2) = (0,2,0); lookfrom = eye + offset - fwd*5 = (10-5, 0+2, 0)
    assert vclose(lookfrom, (5.0, 2.0, 0.0))


# --- _client_fraction_to_ndc: y-flip, inset remap, out-of-canvas rejection ---------------------
def test_fraction_to_ndc_centre_and_corners():
    f = OnshapeBridge._client_fraction_to_ndc
    assert vclose(f(0.5, 0.5, (0, 0, 0, 0)) + (0.0,), (0.0, 0.0, 0.0))
    assert f(0.0, 0.0, (0, 0, 0, 0)) == (-1.0, 1.0)       # top-left -> x=-1, y=+1 (y flipped)
    assert f(1.0, 1.0, (0, 0, 0, 0)) == (1.0, -1.0)       # bottom-right -> x=+1, y=-1


def test_fraction_to_ndc_rejects_outside_canvas():
    f = OnshapeBridge._client_fraction_to_ndc
    assert f(1.5, 0.5, (0, 0, 0, 0)) is None              # far right of the canvas
    assert f(0.5, -0.5, (0, 0, 0, 0)) is None             # above the canvas
    assert f(1.01, 0.5, (0, 0, 0, 0)) is not None         # within the small tolerance -> kept


def test_fraction_to_ndc_inset_remap():
    # canvas inset 20% left, 10% top -> the client fraction at the canvas's own centre is
    # (0.2 + 0.8/2, 0.1 + 0.9/2) = (0.6, 0.55) and must map to NDC centre (0,0).
    f = OnshapeBridge._client_fraction_to_ndc
    assert vclose(f(0.6, 0.55, (0.2, 0.1, 0.0, 0.0)) + (0.0,), (0.0, 0.0, 0.0))
    # the canvas top-left (at client 0.2, 0.1) -> (-1, +1)
    assert vclose(f(0.2, 0.1, (0.2, 0.1, 0.0, 0.0)) + (0.0,), (-1.0, 1.0, 0.0))
    # a cursor left of the canvas inset (over the feature tree) -> rejected
    assert f(0.05, 0.5, (0.2, 0.1, 0.0, 0.0)) is None


# --- auto-left: back the resizable feature-tree panel width out of the view aspect --------------
def test_effective_canvas_inset_auto_left_closed_panel():
    # window aspect == view aspect (4:3) -> canvas fills the width -> left inset 0 (panel closed).
    l, t, r, b = OnshapeBridge._effective_canvas_inset(4.0, 3.0, 800, 600, (0, 0, 0, 0), True)
    assert abs(l) < 1e-9 and (t, r, b) == (0.0, 0.0, 0.0)


def test_effective_canvas_inset_auto_left_tracks_panel():
    # A 25%-wide left panel makes the canvas narrower: canvas aspect drops to 3:3 in an 800x600
    # window (canvas 600x600). The auto-left must recover left = 1 - 600/800 = 0.25.
    l, _t, _r, _b = OnshapeBridge._effective_canvas_inset(3.0, 3.0, 800, 600, (0, 0, 0, 0), True)
    assert abs(l - 0.25) < 1e-9
    # a WIDER panel (canvas 400x600, aspect 2:3) -> left = 1 - 400/800 = 0.5 (offset grows w/ panel)
    l2, _, _, _ = OnshapeBridge._effective_canvas_inset(2.0, 3.0, 800, 600, (0, 0, 0, 0), True)
    assert abs(l2 - 0.5) < 1e-9


def test_effective_canvas_inset_auto_left_uses_top():
    # THE bug the user hit: auto-left needs a CORRECT top or the left is GAINED (offset scales with x).
    # Geometry: a 25% left panel + a 10% top toolbar -> canvas 600x540 in an 800x600 window.
    l_ok, _t, _r, _b = OnshapeBridge._effective_canvas_inset(600.0, 540.0, 800, 600, (0, 0.1, 0, 0), True)
    assert abs(l_ok - 0.25) < 1e-9                        # correct top -> exact left
    l_bad, _, _, _ = OnshapeBridge._effective_canvas_inset(600.0, 540.0, 800, 600, (0, 0.0, 0, 0), True)
    assert abs(l_bad - 0.25) > 0.05                       # top=0 (the old default) -> left is off


def test_effective_canvas_inset_auto_left_disabled():
    assert OnshapeBridge._effective_canvas_inset(2.0, 3.0, 800, 600, (0.1, 0, 0, 0), False) == \
        (0.1, 0.0, 0.0, 0.0)


# --- _hit_cursor end to end: cursor fraction -> ndc -> ray -> hit -------------------------------
def _no_auto(bridge, inset=(0.0, 0.0, 0.0, 0.0)):
    bridge._canvas_auto_left = False          # test the raw mapping (instance field, not the env)
    bridge._canvas_inset = inset


def test_hit_cursor_aims_ray_through_the_cursor(bridge, monkeypatch):
    _no_auto(bridge)
    monkeypatch.setattr(ob, "_cached_canvas_inset", lambda *a, **k: None)   # force config path
    # 6-tuple: fx,fy,win_w,win_h,content_left,content_top
    monkeypatch.setattr(ob, "_cursor_client_fraction",
                        lambda: (0.75, 0.25, 800, 600, 0.0, 0.0))  # ndc (0.5,0.5)
    conn = FakeConn(hit=[1.0, 2.0, 5.0])                  # inside the default model bbox
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    hit = bridge._hit_cursor(conn, eye, right, up, back)
    assert hit == (1.0, 2.0, 5.0)
    # half_x=4, half_y=3, vh=4, backoff=32 -> offset (0.5*4, 0.5*3) = (2, 1.5)
    assert vclose(conn.write_of("hit.lookfrom"), (2.0, 1.5, 50.0 + 32.0))
    assert vclose(conn.write_of("hit.direction"), (0.0, 0.0, -1.0))


def test_hit_cursor_auto_left_shifts_the_ray(bridge, monkeypatch):
    # With a 25% left panel (auto-left on), a cursor at client-fraction 0.625 sits at the canvas
    # centre (0.25 + 0.75/2) -> ndc.x ~ 0 -> the ray is NOT offset right, proving auto-left corrects
    # the panel. Window 800x600, view aspect 3:3 -> auto-left = 0.25.
    bridge._canvas_auto_left = True
    bridge._canvas_inset = (0.0, 0.0, 0.0, 0.0)
    monkeypatch.setattr(ob, "_cached_canvas_inset", lambda *a, **k: None)
    monkeypatch.setattr(ob, "_cursor_client_fraction",
                        lambda: (0.625, 0.5, 800, 600, 0.0, 0.0))
    conn = FakeConn(hit=[0.0, 0.0, 5.0], view_ext=[-3.0, -3.0, -1.0, 3.0, 3.0, 1.0])
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    bridge._hit_cursor(conn, eye, right, up, back)
    lf = conn.write_of("hit.lookfrom")
    assert abs(lf[0]) < 1e-9 and abs(lf[1]) < 1e-9         # centred: no right/up offset


def test_hit_cursor_uses_measured_inset(bridge, monkeypatch):
    """Measured canvas rect wins over config/auto-left -- the path that fixes Top=0 gain-with-x."""
    bridge._canvas_auto_left = True
    bridge._canvas_inset = (0.0, 0.0, 0.0, 0.0)            # would wrongly put left=0
    monkeypatch.setattr(ob, "_cached_canvas_inset",
                        lambda *a, **k: (0.25, 0.1, 0.0, 0.0))  # measured 25% left + 10% top
    # cursor at canvas centre of the measured rect: fx=0.25+0.75/2=0.625, fy=0.1+0.9/2=0.55
    monkeypatch.setattr(ob, "_cursor_client_fraction",
                        lambda: (0.625, 0.55, 800, 600, 0.0, 0.0))
    conn = FakeConn(hit=[0.0, 0.0, 5.0], view_ext=[-3.0, -3.0, -1.0, 3.0, 3.0, 1.0])
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    bridge._hit_cursor(conn, eye, right, up, back)
    lf = conn.write_of("hit.lookfrom")
    assert abs(lf[0]) < 1e-9 and abs(lf[1]) < 1e-9


def test_hit_cursor_none_when_cursor_off_canvas(bridge, monkeypatch):
    _no_auto(bridge)
    monkeypatch.setattr(ob, "_cached_canvas_inset", lambda *a, **k: None)
    monkeypatch.setattr(ob, "_cursor_client_fraction",
                        lambda: (1.4, 0.5, 800, 600, 0.0, 0.0))  # right of canvas
    conn = FakeConn(hit=[1.0, 2.0, 5.0])
    assert bridge._hit_cursor(conn, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) is None
    assert conn.writes == []                              # never touched the hit-test


def test_hit_cursor_none_when_no_cursor(bridge, monkeypatch):
    monkeypatch.setattr(ob, "_cursor_client_fraction", lambda: None)          # off-Windows / no win
    conn = FakeConn(hit=[1.0, 2.0, 5.0])
    assert bridge._hit_cursor(conn, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) is None
    assert conn.writes == []


def test_hit_cursor_bbox_rejects_stray_hit(bridge, monkeypatch):
    _no_auto(bridge)
    monkeypatch.setattr(ob, "_cached_canvas_inset", lambda *a, **k: None)
    monkeypatch.setattr(ob, "_cursor_client_fraction",
                        lambda: (0.5, 0.5, 800, 600, 0.0, 0.0))
    conn = FakeConn(hit=[500.0, 0.0, 0.0])               # far outside model.extents
    assert bridge._hit_cursor(conn, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) is None
    # every aperture tried a read (all rejected by the bbox guard)
    reads = [w for w in conn.writes if w[0] == "hit.aperture"]
    assert len(reads) == len(ob.HIT_APERTURES)


def test_set_canvas_updates_live():
    b = OnshapeBridge()
    b.set_canvas([0.1, 0.06, 0.0, 0.0], False)
    assert b._canvas_inset == (0.1, 0.06, 0.0, 0.0) and b._canvas_auto_left is False
    b.set_canvas(None, True)                              # None leaves the inset, updates auto_left
    assert b._canvas_inset == (0.1, 0.06, 0.0, 0.0) and b._canvas_auto_left is True


# --- _pivot("cursor"): cursor hit -> centre hit -> model centre ---------------------------------
def test_pivot_cursor_uses_cursor_hit(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_hit_cursor", lambda *a: (1.0, 1.0, 1.0))
    monkeypatch.setattr(bridge, "_hit_center",
                        lambda *a: (_ for _ in ()).throw(AssertionError("should not reach centre")))
    p = bridge._pivot(FakeConn(), {"op": "cursor"}, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert p == (1.0, 1.0, 1.0)


def test_pivot_cursor_falls_back_to_centre_then_object(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_hit_cursor", lambda *a: None)               # cursor miss
    monkeypatch.setattr(bridge, "_hit_center", lambda *a: (7.0, 7.0, 7.0))    # centre hits
    p = bridge._pivot(FakeConn(), {"op": "cursor"}, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert p == (7.0, 7.0, 7.0)

    monkeypatch.setattr(bridge, "_hit_center", lambda *a: None)               # centre also misses
    monkeypatch.setattr(bridge, "_object_center", lambda conn: (9.0, 9.0, 9.0))
    p = bridge._pivot(FakeConn(), {"op": "cursor"}, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert p == (9.0, 9.0, 9.0)


def test_pivot_view_never_touches_cursor(bridge, monkeypatch):
    # regression: the refactor must not route "view"/"selection" through the cursor path
    monkeypatch.setattr(bridge, "_hit_center", lambda *a: (3.0, 3.0, 3.0))
    monkeypatch.setattr(bridge, "_hit_cursor",
                        lambda *a: (_ for _ in ()).throw(AssertionError("wrong path")))
    assert bridge._pivot(FakeConn(), {"op": "view"},
                         (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) == (3.0, 3.0, 3.0)
    assert bridge._pivot(FakeConn(), {"op": "selection"},
                         (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) == (3.0, 3.0, 3.0)


# --- Firefox content-top: chrome-included client rect is the sizing bug -----------------------
def test_firefox_content_top_finds_dark_page_row(monkeypatch):
    """Synthetic screen-DC band: light Firefox chrome, then a full-width Onshape-dark row.
    The detector must return that row (not an earlier sparse dark speck on the right).
    Also rejects an all-black capture (the old GetDC(hwnd) BitBlt failure mode)."""
    import ctypes
    from ctypes import wintypes
    w, band, page_y = 200, 80, 40
    buf = bytearray(w * band * 4)
    for y in range(band):
        for x in range(w):
            i = (y * w + x) * 4
            if y >= page_y:
                buf[i:i + 3] = bytes((34, 27, 28))       # B,G,R ~ Onshape (28,27,34)
            else:
                buf[i:i + 3] = bytes((209, 180, 153))    # light Firefox tab blue
            buf[i + 3] = 255
    for y in range(5, 15):
        i = (y * w + (w - 2)) * 4
        buf[i:i + 3] = bytes((34, 27, 28))

    monkeypatch.setattr(ob, "_capture_screen_bgra",
                        lambda *_a, **_k: (buf, w, band))

    class FakeU32:
        def ClientToScreen(self, _hwnd, pt_ref):
            pt = ctypes.cast(pt_ref, ctypes.POINTER(wintypes.POINT)).contents
            pt.x, pt.y = 0, 0
            return 1

    class FakeWindll:
        user32 = FakeU32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    ob._FF_CONTENT_CACHE = {"key": None, "top": 0}
    assert ob._firefox_content_top(hwnd=42, client_w=w, client_h=band + 400) == page_y
    assert ob._firefox_content_top(hwnd=42, client_w=w, client_h=band + 400) == page_y  # cache


def test_firefox_content_top_rejects_black_capture(monkeypatch):
    """GetDC(hwnd) BitBlt on Firefox returns solid black -- must NOT report content_top>0."""
    import ctypes
    from ctypes import wintypes
    w, band = 200, 80
    buf = bytearray(w * band * 4)          # all zeros = black
    monkeypatch.setattr(ob, "_capture_screen_bgra", lambda *_a, **_k: (buf, w, band))

    class FakeU32:
        def ClientToScreen(self, _hwnd, pt_ref):
            pt = ctypes.cast(pt_ref, ctypes.POINTER(wintypes.POINT)).contents
            pt.x, pt.y = 0, 0
            return 1

    class FakeWindll:
        user32 = FakeU32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    ob._FF_CONTENT_CACHE = {"key": None, "top": 0}
    assert ob._firefox_content_top(hwnd=42, client_w=w, client_h=band + 400) == 0


def test_measure_canvas_inset_finds_panel_and_toolbar(monkeypatch):
    """Synthetic content bitmap: left panel + top toolbar + canvas; width from view aspect."""
    w, h = 400, 300
    # toolbar 30px, canvas 270px tall flush right; with aspect 300/270 left = 100px panel
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            if y < 30:
                buf[i:i + 3] = bytes((34, 27, 28))       # toolbar UI-dark
            elif x < 100:
                buf[i:i + 3] = bytes((34, 27, 28))       # left panel UI-dark
            else:
                buf[i:i + 3] = bytes((51, 51, 51))       # canvas grey
            buf[i + 3] = 255
    monkeypatch.setattr(ob, "_capture_screen_bgra", lambda *_a, **_k: (buf, w, h))
    inset = ob._measure_canvas_inset(0, 0, w, h, half_x=300.0, half_y=270.0)
    assert inset is not None
    l, t, r, b = inset
    assert abs(l - 0.25) < 0.05                          # ~100/400 from aspect
    assert abs(t - 0.10) < 0.05                          # ~30/300 from column scan
    assert r < 0.02 and b < 0.05


def test_measure_canvas_inset_works_when_model_fills_view(monkeypatch):
    """Model colours on the canvas must not break the right-column top/bottom scan."""
    w, h = 400, 300
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            if y < 30 or x < 100:
                buf[i:i + 3] = bytes((34, 27, 28))
            elif 150 <= x <= 250 and 100 <= y <= 200:
                buf[i:i + 3] = bytes((140, 185, 212))    # blue cube
            else:
                buf[i:i + 3] = bytes((51, 51, 51))
            buf[i + 3] = 255
    monkeypatch.setattr(ob, "_capture_screen_bgra", lambda *_a, **_k: (buf, w, h))
    inset = ob._measure_canvas_inset(0, 0, w, h, half_x=300.0, half_y=270.0)
    assert inset is not None
    assert abs(inset[0] - 0.25) < 0.05 and abs(inset[1] - 0.10) < 0.05


def test_browser_content_rect_prefers_chromium_render_widget(monkeypatch):
    """Chromium: largest Chrome_RenderWidgetHostHWND wins (already excludes browser chrome)."""
    monkeypatch.setattr(ob, "_find_largest_descendant", lambda *_a, **_k: 99)
    monkeypatch.setattr(ob, "_firefox_content_top",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ff path")))

    import ctypes
    from ctypes import wintypes

    calls = {"client": 0, "screen": 0}

    def fake_client(hwnd, rc_ref):
        calls["client"] += 1
        # byref(RECT) -> write into the pointed-to RECT
        rc = ctypes.cast(rc_ref, ctypes.POINTER(wintypes.RECT)).contents
        rc.left, rc.top, rc.right, rc.bottom = 0, 0, 1600, 900
        return 1

    def fake_screen(hwnd, pt_ref):
        calls["screen"] += 1
        pt = ctypes.cast(pt_ref, ctypes.POINTER(wintypes.POINT)).contents
        pt.x, pt.y = 10, 80
        return 1

    class FakeU32:
        GetClientRect = staticmethod(fake_client)
        ClientToScreen = staticmethod(fake_screen)

        def GetClassNameW(self, *_a):
            raise AssertionError("Chromium path should not need the class name")

    class FakeWindll:
        user32 = FakeU32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    assert ob._browser_content_rect(1) == (10, 80, 1600, 900)
    assert calls["client"] == 1 and calls["screen"] == 1


def test_chrome_included_client_gains_auto_left():
    """The Firefox sizing bug: MozillaWindowClass's client rect includes browser chrome, but
    canvas_inset.top is an Onshape-toolbar fraction of the *page*. Feeding auto-left the
    chrome-inflated height with a content-calibrated top makes left drift (horizontal scale
    error); the unsubtracted chrome also shifts fy downward (vertical offset)."""
    # True (content-relative): 800x600 page, 25% left panel, 10% top toolbar, canvas 600x540.
    l_ok, _, _, _ = OnshapeBridge._effective_canvas_inset(
        600.0, 540.0, 800, 600, (0.0, 0.1, 0.0, 0.0), True)
    assert abs(l_ok - 0.25) < 1e-9
    # Bug: same top=0.1 (toolbar/page) but win_h still includes 100 px of browser chrome.
    l_bad, _, _, _ = OnshapeBridge._effective_canvas_inset(
        600.0, 540.0, 800, 700, (0.0, 0.1, 0.0, 0.0), True)
    assert abs(l_bad - 0.25) > 0.03
    # Vertical: a cursor on the canvas top edge is at content fy=0.1, but relative to the
    # chrome-included client it's (100+60)/700 -- maps below the toolbar NDC.
    ndc_ok = OnshapeBridge._client_fraction_to_ndc(0.625, 0.1, (0.25, 0.1, 0.0, 0.0))
    ndc_bad = OnshapeBridge._client_fraction_to_ndc(0.625, 160 / 700, (0.25, 0.1, 0.0, 0.0))
    assert ndc_ok is not None and abs(ndc_ok[1] - 1.0) < 1e-9          # canvas top -> NDC y=+1
    assert ndc_bad is not None and ndc_bad[1] < 0.95                    # shifted down
