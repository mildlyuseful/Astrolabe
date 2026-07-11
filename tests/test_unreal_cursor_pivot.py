"""Headless unit tests for the Unreal add-on's "cursor" orbit pivot / "to_cursor" zoom.

``trackball_nav`` imports ``unreal`` at module level, so these tests inject a minimal stub into
``sys.modules`` before import — enough for the GeoReferencing Half A call, ``screen_to_world``,
``line_trace_single``, and HitResult ``to_dict``. That proves the offline pipeline: viewport mouse
-> world ray -> bbox-validated pivot, the per-gesture hold, and the fallbacks. It cannot prove that
``GeoReferencingEditorBPLibrary`` returns a live pixel in a focused level viewport (see
docs/apps/unreal.md gotcha #13).
"""
import os
import sys
import types

import pytest

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_ADDIN_PY = os.path.join(
    _HERE, "..", "trackball_daemon", "plugins", "unreal", "TrackballNav", "Content", "Python")


class _V2:
    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)


class _V3:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Hit:
    def __init__(self, impact):
        self._impact = impact

    def to_dict(self):
        return {"impact_point": _V3(*self._impact), "location": _V3(*self._impact)}


class _GeoLib:
    """Stand-in for unreal.GeoReferencingEditorBPLibrary."""
    focused = True
    screen = (100.0, 150.0)
    origin = (0.0, 0.0, 100.0)
    direction = (0.0, 0.0, -1.0)
    raise_info = False
    use_location_only = False

    @classmethod
    def get_viewport_cursor_information(cls):
        if cls.raise_info:
            raise RuntimeError("boom")
        return (cls.focused, _V2(*cls.screen), _V3(*cls.origin), _V3(*cls.direction))

    @classmethod
    def get_viewport_cursor_location(cls):
        return (cls.focused, _V2(*cls.screen))


class _Subsystem:
    screen_to_world_result = ((_V3(0.0, 0.0, 100.0), _V3(0.0, 0.0, -1.0)))

    def get_editor_world(self):
        return object()

    def get_game_world(self):
        return None

    def screen_to_world(self, _v2):
        return self.screen_to_world_result


class _SystemLibrary:
    hit = _Hit((2.0, 3.0, 4.0))
    calls = []

    @classmethod
    def line_trace_single(cls, world, start, end, *_a, **_k):
        cls.calls.append((start.x, start.y, start.z, end.x, end.y, end.z))
        return cls.hit

    @staticmethod
    def get_engine_version():
        return "5.8.0-test"


def _install_fake_unreal():
    unreal = types.ModuleType("unreal")
    unreal.Vector = _V3
    unreal.Vector2D = _V2
    unreal.Rotator = lambda **kw: types.SimpleNamespace(**kw)
    unreal.GeoReferencingEditorBPLibrary = _GeoLib
    unreal.SystemLibrary = _SystemLibrary
    unreal.TraceTypeQuery = types.SimpleNamespace(TRACE_TYPE_QUERY1=1)
    unreal.DrawDebugTrace = types.SimpleNamespace(NONE=0)
    unreal.UnrealEditorSubsystem = object
    unreal.EditorActorSubsystem = object
    unreal.MathLibrary = types.SimpleNamespace(
        get_forward_vector=lambda r: _V3(1, 0, 0),
        get_right_vector=lambda r: _V3(0, 1, 0),
        get_up_vector=lambda r: _V3(0, 0, 1),
        make_rot_from_xz=lambda f, u: types.SimpleNamespace(pitch=0, yaw=0, roll=0),
    )

    sub = _Subsystem()

    def get_editor_subsystem(cls):
        if cls is unreal.UnrealEditorSubsystem:
            return sub
        if cls is unreal.EditorActorSubsystem:
            return types.SimpleNamespace(get_selected_level_actors=lambda: [])
        return None

    unreal.get_editor_subsystem = get_editor_subsystem
    unreal._test_sub = sub  # stash for tests
    sys.modules["unreal"] = unreal
    return unreal


_install_fake_unreal()
sys.path.insert(0, _ADDIN_PY)
import trackball_nav as tn  # noqa: E402


PTR_HIT = (2.0, 3.0, 4.0)
BBOX = ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))


def _cam():
    return tn.cammath.Camera((0.0, 0.0, 100.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def test_action_axis_routing_can_make_twist_drive_walk_forward():
    adv = {
        "axis_source": {"walk": {
            "pitch": 0, "yaw": 1, "forward": 2, "strafe": 0, "vertical": 1}},
        "invert": {"walk": {"forward": True}},
    }
    o, p, z = tn._apply_action_routing("walk", "camera", [1.0, 2.0, 3.0],
                                       [4.0, 5.0], 6.0, adv)
    assert o == [1.0, 2.0, 3.0]
    assert p == [4.0, -6.0]       # movement Z (twist) -> forward, then inverted
    assert z == 5.0               # movement Y -> vertical


def test_action_axis_routing_defaults_are_bit_identical():
    o, p, z = tn._apply_action_routing("fly", "camera", [1.0, 2.0, 3.0],
                                       [4.0, 5.0], 6.0, {})
    assert (o, p, z) == ([1.0, 2.0, 3.0], [4.0, 5.0], 6.0)


@pytest.fixture(autouse=True)
def _reset():
    tn._gesture.update(t=0.0, pivot=None, invalid=True)
    tn._zoom_gesture.update(pivot=None)
    tn._obj_cache.update(t=0.0, center=None, bbox=None)
    tn._georef_logged["missing"] = False
    _GeoLib.focused = True
    _GeoLib.screen = (100.0, 150.0)
    _GeoLib.origin = (0.0, 0.0, 100.0)
    _GeoLib.direction = (0.0, 0.0, -1.0)
    _GeoLib.raise_info = False
    _GeoLib.use_location_only = False
    _SystemLibrary.hit = _Hit(PTR_HIT)
    _SystemLibrary.calls = []
    sys.modules["unreal"]._test_sub.screen_to_world_result = (
        (_V3(0.0, 0.0, 100.0), _V3(0.0, 0.0, -1.0)))
    yield


def test_cursor_screen_ray_from_georef_information():
    ray = tn._cursor_screen_ray()
    assert ray is not None
    px, origin, direction = ray
    assert px == (100.0, 150.0)
    assert origin == (0.0, 0.0, 100.0)
    assert direction == (0.0, 0.0, -1.0)


def test_cursor_screen_ray_none_when_viewport_unfocused():
    _GeoLib.focused = False
    assert tn._cursor_screen_ray() is None


def test_cursor_screen_ray_falls_back_to_location_plus_screen_to_world():
    _GeoLib.raise_info = True
    ray = tn._cursor_screen_ray()
    assert ray is not None
    assert ray[0] == (100.0, 150.0)
    assert ray[2] == (0.0, 0.0, -1.0)


def test_cursor_screen_ray_none_without_georef_type(monkeypatch):
    monkeypatch.setattr(tn.unreal, "GeoReferencingEditorBPLibrary", None)
    assert tn._cursor_screen_ray() is None
    assert tn._georef_logged["missing"] is True


def test_cursor_pivot_traces_georef_ray_not_camera_forward():
    _GeoLib.origin = (5.0, 5.0, 50.0)
    _GeoLib.direction = (0.0, 1.0, 0.0)
    assert tn._cursor_pivot(BBOX) == PTR_HIT
    assert _SystemLibrary.calls, "must line_trace"
    start = _SystemLibrary.calls[0][:3]
    assert start == (5.0, 5.0, 50.0)


def test_cursor_pivot_none_without_mouse():
    _GeoLib.focused = False
    assert tn._cursor_pivot(BBOX) is None


def test_cursor_pivot_rejects_hit_outside_bbox():
    _SystemLibrary.hit = _Hit((100.0, 100.0, 100.0))
    assert tn._cursor_pivot(BBOX) is None


def test_cursor_pivot_accepts_far_hit_when_bbox_is_none():
    _SystemLibrary.hit = _Hit((100.0, 100.0, 100.0))
    assert tn._cursor_pivot(None) == (100.0, 100.0, 100.0)


def test_orbit_pivot_cursor_holds_for_the_gesture():
    assert tn._orbit_pivot("cursor", _cam(), idle=10.0) == PTR_HIT
    _SystemLibrary.hit = _Hit((6.0, 6.0, 6.0))
    _GeoLib.screen = (200.0, 200.0)
    assert tn._orbit_pivot("cursor", _cam(), idle=0.0) == PTR_HIT  # held
    assert tn._orbit_pivot("cursor", _cam(), idle=tn.PIVOT_HOLD_IDLE + 0.01) == (6.0, 6.0, 6.0)


def test_orbit_pivot_cursor_falls_back_to_forward_on_miss(monkeypatch):
    # Miss with nothing selected -> forward point (selection centre is no longer the miss fallback
    # unless sel_override + a live selection, covered separately).
    monkeypatch.setattr(tn, "_selection_center", lambda: (None, None))
    _SystemLibrary.hit = None
    assert tn._orbit_pivot("cursor", _cam(), idle=10.0) == tn._forward_point(_cam())


def test_selection_override_wins_over_cursor_hit(monkeypatch):
    monkeypatch.setattr(tn, "_selection_center", lambda: ((5.0, 5.0, 5.0), BBOX))
    # Cursor would hit PTR_HIT, but with override on + a selection, selection centre wins.
    assert tn._orbit_pivot("cursor", _cam(), idle=10.0, sel_override=True) == (5.0, 5.0, 5.0)
    assert _SystemLibrary.calls == []


def test_selection_override_off_keeps_cursor_hit(monkeypatch):
    monkeypatch.setattr(tn, "_selection_center", lambda: ((5.0, 5.0, 5.0), BBOX))
    assert tn._orbit_pivot("cursor", _cam(), idle=10.0, sel_override=False) == PTR_HIT


def test_selection_override_off_ignores_bbox_gate(monkeypatch):
    # Hit far outside the selection bbox — with override off the bbox gate is disabled.
    monkeypatch.setattr(tn, "_selection_center", lambda: ((5.0, 5.0, 5.0), BBOX))
    _SystemLibrary.hit = _Hit((100.0, 100.0, 100.0))
    assert tn._orbit_pivot("cursor", _cam(), idle=10.0, sel_override=False) == (100.0, 100.0, 100.0)


def test_selection_override_on_rejects_hit_outside_bbox_then_uses_selection(monkeypatch):
    monkeypatch.setattr(tn, "_selection_center", lambda: ((5.0, 5.0, 5.0), BBOX))
    # With override on, selection wins immediately — no raycast.
    assert tn._orbit_pivot("screen_center", _cam(), idle=10.0,
                           sel_override=True) == (5.0, 5.0, 5.0)


def test_zoom_to_cursor_honours_selection_override(monkeypatch):
    monkeypatch.setattr(tn, "_selection_center", lambda: ((5.0, 5.0, 5.0), BBOX))
    assert tn._zoom_toward("to_cursor", idle=10.0, sel_override=True) == (5.0, 5.0, 5.0)
    assert tn._zoom_toward("to_cursor", idle=10.0, sel_override=False) == PTR_HIT


def test_orbit_pivot_object_does_not_use_cursor(monkeypatch):
    monkeypatch.setattr(tn, "_selection_center", lambda: ((9.0, 9.0, 9.0), BBOX))
    _SystemLibrary.calls = []
    assert tn._orbit_pivot("object", _cam(), idle=10.0) == (9.0, 9.0, 9.0)
    assert _SystemLibrary.calls == []


def test_zoom_toward_to_cursor_holds_and_falls_back():
    assert tn._zoom_toward("to_cursor", idle=10.0) == PTR_HIT
    _SystemLibrary.hit = _Hit((6.0, 6.0, 6.0))
    assert tn._zoom_toward("to_cursor", idle=0.0) == PTR_HIT
    assert tn._zoom_toward("to_cursor", idle=tn.PIVOT_HOLD_IDLE + 0.01) == (6.0, 6.0, 6.0)
    tn._zoom_gesture["pivot"] = None
    _SystemLibrary.hit = None
    assert tn._zoom_toward("to_cursor", idle=10.0) is None


def test_zoom_toward_to_center_is_none():
    assert tn._zoom_toward("to_center", idle=10.0) is None


def test_screen_center_pivot_uses_camera_forward():
    cam = _cam()
    assert tn._screen_center_pivot(cam, BBOX) == PTR_HIT
    start = _SystemLibrary.calls[0][:3]
    assert start == tuple(cam.location)
