"""Headless unit tests for the Onshape bridge's under-cursor orbit pivot.

The bridge drives Onshape in a browser. Exact canvas size comes from a page userscript that
POSTs #canvas-relative NDC to /trackball/pointer (no screen capture, no view.extents aspect).
These tests cover the deterministic offline pieces with a fake connection:

  * _pixel_ray         -- NDC canvas point -> world pick ray
  * _parse_pointer_body / _set/_get_page_pointer -- page report ingest + TTL / off-canvas
  * _hit_cursor        -- page NDC -> ray -> hit
  * _pivot("cursor")   -- cursor hit, then centre, then model centre; view never uses cursor

What it CANNOT prove (needs the live browser + userscript): that the page report lands on the
real WebGL canvas and that Onshape's hit-test returns the surface under the mouse.
"""
import math
import os
import sys
import time

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from trackball_daemon import onshape_bridge as ob  # noqa: E402
from trackball_daemon.onshape_bridge import OnshapeBridge, _PropUnsupported  # noqa: E402


class FakeConn:
    def __init__(self, hit=None, view_ext=None, model_ext=None, misses=0,
                 selection_ext=None, selection_empty=None):
        self._hit_unsupported = False
        self._hit = hit
        self._view_ext = view_ext if view_ext is not None else [-4.0, -3.0, -1.0, 4.0, 3.0, 1.0]
        self._model_ext = model_ext if model_ext is not None else [-10.0] * 3 + [10.0] * 3
        self.misses = misses
        self._selection_ext = selection_ext
        self._selection_empty = selection_empty
        self.writes = []

    def read(self, prop, ttl=0.0):
        if prop == "view.extents":
            return self._view_ext
        if prop == "model.extents":
            return self._model_ext
        if prop == "selection.extents":
            return self._selection_ext
        if prop == "selection.empty":
            return self._selection_empty
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


@pytest.fixture(autouse=True)
def _clear_page_pointer():
    with ob._PAGE_POINTER_LOCK:
        ob._PAGE_POINTER.update({"t": 0.0, "ndc_x": 0.0, "ndc_y": 0.0, "on": False})
    yield
    with ob._PAGE_POINTER_LOCK:
        ob._PAGE_POINTER.update({"t": 0.0, "ndc_x": 0.0, "ndc_y": 0.0, "on": False})


def vclose(u, v, eps=1e-9):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


# --- _pixel_ray -------------------------------------------------------------------------------
def test_pixel_ray_centre_matches_screen_centre():
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    lookfrom, direction = OnshapeBridge._pixel_ray(0.0, 0.0, eye, right, up, back, 4.0, 3.0, 40.0)
    assert vclose(direction, (0.0, 0.0, -1.0))
    assert vclose(lookfrom, (0.0, 0.0, 90.0))


def test_pixel_ray_offcentre_offsets_along_right_up():
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    lookfrom, direction = OnshapeBridge._pixel_ray(0.5, 0.5, eye, right, up, back, 4.0, 3.0, 40.0)
    assert vclose(direction, (0.0, 0.0, -1.0))
    assert vclose(lookfrom, (2.0, 1.5, 90.0))


def test_pixel_ray_uses_the_camera_basis():
    eye, right, up, back = (10.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)
    lookfrom, direction = OnshapeBridge._pixel_ray(1.0, 0.0, eye, right, up, back, 2.0, 2.0, 5.0)
    assert vclose(direction, (1.0, 0.0, 0.0))
    assert vclose(lookfrom, (5.0, 2.0, 0.0))


# --- page pointer ingest ----------------------------------------------------------------------
def test_parse_pointer_body_ndc():
    assert ob._parse_pointer_body(b'{"ndc_x":0.5,"ndc_y":-0.25,"on_canvas":true}') == \
        (0.5, -0.25, True)
    assert ob._parse_pointer_body('{"ndc_x":0,"ndc_y":0,"on_canvas":false}')[2] is False


def test_parse_pointer_body_xywh():
    # canvas-local CSS px -> NDC (centre of 200x100)
    parsed = ob._parse_pointer_body(b'{"x":100,"y":50,"w":200,"h":100}')
    assert parsed is not None
    assert abs(parsed[0]) < 1e-9 and abs(parsed[1]) < 1e-9 and parsed[2] is True
    # top-left
    parsed = ob._parse_pointer_body(b'{"x":0,"y":0,"w":200,"h":100}')
    assert vclose((parsed[0], parsed[1], 0.0), (-1.0, 1.0, 0.0))


def test_parse_pointer_body_rejects_junk():
    assert ob._parse_pointer_body(b"not-json") is None
    assert ob._parse_pointer_body(b"{}") is None
    assert ob._parse_pointer_body(b'{"ndc_x":1}') is None


def test_page_pointer_ttl_and_off_canvas():
    ob._set_page_pointer(0.25, -0.5, True)
    assert ob._get_page_pointer(ttl=1.0) == (0.25, -0.5)
    ob._set_page_pointer(0.1, 0.1, False)                 # over a panel
    assert ob._get_page_pointer(ttl=1.0) is None
    ob._set_page_pointer(0.0, 0.0, True)
    with ob._PAGE_POINTER_LOCK:
        ob._PAGE_POINTER["t"] = time.monotonic() - 5.0    # stale
    assert ob._get_page_pointer(ttl=0.75) is None


def test_view_extents_aspect_is_not_canvas_aspect():
    """Regression lock: live Onshape reported view.extents aspect ~0.975 while #canvas was
    ~1.72. Auto-left from extents is therefore wrong -- we must not revive it."""
    # Historical logged extents (daemon.log 2026-07-06):
    hx = 0.04564726303841691
    hy = 0.04679519716808124
    extents_aspect = hx / hy
    canvas_aspect = 1674.0 / 974.0                        # live CDP getBoundingClientRect
    assert abs(extents_aspect - 0.9755) < 1e-3
    assert abs(canvas_aspect - 1.7187) < 1e-3
    assert abs(extents_aspect - canvas_aspect) > 0.5       # not interchangeable


# --- _hit_cursor ------------------------------------------------------------------------------
def test_hit_cursor_aims_ray_through_page_ndc(bridge):
    ob._set_page_pointer(0.5, 0.5, True)
    conn = FakeConn(hit=[1.0, 2.0, 5.0])
    eye, right, up, back = (0.0, 0.0, 50.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    hit = bridge._hit_cursor(conn, eye, right, up, back)
    assert hit == (1.0, 2.0, 5.0)
    # half_x=4, half_y=3, vh=4, backoff=32 -> offset (0.5*4, 0.5*3) = (2, 1.5)
    assert vclose(conn.write_of("hit.lookfrom"), (2.0, 1.5, 50.0 + 32.0))
    assert vclose(conn.write_of("hit.direction"), (0.0, 0.0, -1.0))


def test_hit_cursor_none_when_no_page_sample(bridge):
    conn = FakeConn(hit=[1.0, 2.0, 5.0])
    assert bridge._hit_cursor(conn, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) is None
    assert conn.writes == []


def test_hit_cursor_none_when_off_canvas(bridge):
    ob._set_page_pointer(0.0, 0.0, False)
    conn = FakeConn(hit=[1.0, 2.0, 5.0])
    assert bridge._hit_cursor(conn, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) is None
    assert conn.writes == []


def test_hit_cursor_bbox_rejects_stray_hit(bridge):
    ob._set_page_pointer(0.0, 0.0, True)
    conn = FakeConn(hit=[500.0, 0.0, 0.0])
    assert bridge._hit_cursor(conn, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) is None
    reads = [w for w in conn.writes if w[0] == "hit.aperture"]
    assert len(reads) == len(ob.HIT_APERTURES)


# --- _pivot("cursor") -------------------------------------------------------------------------
def test_pivot_cursor_uses_cursor_hit(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_hit_cursor", lambda *a: (1.0, 1.0, 1.0))
    monkeypatch.setattr(bridge, "_hit_center",
                        lambda *a: (_ for _ in ()).throw(AssertionError("should not reach centre")))
    p = bridge._pivot(FakeConn(), {"op": "cursor"}, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert p == (1.0, 1.0, 1.0)


def test_pivot_cursor_falls_back_to_centre_then_object(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_hit_cursor", lambda *a: None)
    monkeypatch.setattr(bridge, "_hit_center", lambda *a: (7.0, 7.0, 7.0))
    p = bridge._pivot(FakeConn(), {"op": "cursor"}, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert p == (7.0, 7.0, 7.0)

    monkeypatch.setattr(bridge, "_hit_center", lambda *a: None)
    monkeypatch.setattr(bridge, "_object_center", lambda conn: (9.0, 9.0, 9.0))
    p = bridge._pivot(FakeConn(), {"op": "cursor"}, (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert p == (9.0, 9.0, 9.0)


def test_pivot_view_never_touches_cursor(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_hit_center", lambda *a: (3.0, 3.0, 3.0))
    monkeypatch.setattr(bridge, "_hit_cursor",
                        lambda *a: (_ for _ in ()).throw(AssertionError("wrong path")))
    assert bridge._pivot(FakeConn(), {"op": "view"},
                         (0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1)) == (3.0, 3.0, 3.0)


def test_selection_override_wins_and_can_be_disabled(bridge, monkeypatch):
    conn = FakeConn(selection_ext=[2.0, 4.0, 6.0, 6.0, 8.0, 10.0], selection_empty=False)
    monkeypatch.setattr(bridge, "_hit_center", lambda *a: (1.0, 1.0, 1.0))
    camera = ((0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert bridge._pivot(conn, {"op": "view", "sel_override": True}, *camera) == (4.0, 6.0, 8.0)
    assert bridge._pivot(conn, {"op": "view", "sel_override": False}, *camera) == (1.0, 1.0, 1.0)


def test_designated_selection_works_when_override_is_off(bridge):
    conn = FakeConn(selection_ext=[0.0, 2.0, 4.0, 2.0, 6.0, 8.0], selection_empty=False)
    camera = ((0, 0, 50), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert bridge._pivot(conn, {"op": "selection", "sel_override": False}, *camera) == (1.0, 4.0, 6.0)


def test_userscript_mentions_endpoint_and_canvas():
    assert "127.51.68.120:8181/trackball/pointer" in ob._POINTER_USERSCRIPT
    assert 'getElementById("canvas")' in ob._POINTER_USERSCRIPT
    assert "getBoundingClientRect" in ob._POINTER_USERSCRIPT
    assert ob.pointer_userscript_source() == ob._POINTER_USERSCRIPT
    steps = ob.pointer_install_instructions()
    assert "Tampermonkey" in steps and "Violentmonkey" in steps
    assert "new script" in steps.lower() or "Create a new script" in steps
    assert ob.POINTER_SCRIPT_URL in steps
