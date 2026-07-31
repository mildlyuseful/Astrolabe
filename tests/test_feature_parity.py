# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""UI capability declarations must match behavior already present in each host integration."""
from pathlib import Path

from trackball_daemon.app_registry import APP_BINDING_PROFILES
from trackball_daemon.ui import _free_orbit_needs_roll_warning


ROOT = Path(__file__).parents[1]


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_every_advertised_model_center_has_a_non_selection_runtime_path():
    checks = {
        "blender": ("trackball_daemon/plugins/blender/trackball_nav/__init__.py",
                    'elif method == "object":\n            point = _object_center()'),
        "freecad": ("trackball_daemon/plugins/freecad/TrackballNav/tbnav_freecad.py",
                    'elif method == "object":\n            point = center'),
        "fusion360": ("trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py",
                      'elif method == "object":\n            point = _object_center(None)'),
        "sketchup": ("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb",
                     "when 'object' then object_center(model, nil)"),
        "unreal": ("trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py",
                   'elif method == "object":\n            point = _scene_center()'),
        "unity": ("trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs",
                  'method == "object") point = SceneCenter()'),
        "rhino": ("trackball_daemon/plugins/rhino/TrackballNav/tbnav_rhino.py",
                  'elif method == "object":\n            point = _document_center()'),
        "autocad": ("plugin_src/autocad/TrackballNavAcad/Plugin.cs",
                    'case "object": point = CaptureDrawingCenter()'),
        "solidworks": ("trackball_daemon/solidworks_driver.py",
                       'elif method == "object":\n            point = self._object_center(model)'),
        "onshape": ("trackball_daemon/onshape_bridge.py",
                    'elif method == "object":\n                point = self._object_center(conn)'),
    }
    assert set(checks) == set(APP_BINDING_PROFILES)
    for key, (path, token) in checks.items():
        assert "object" in APP_BINDING_PROFILES[key].pivots
        assert token in _source(path), f"{key} advertises Model Center without its own object path"


def test_every_advertised_to_object_zoom_has_a_model_bounds_consumer():
    checks = {
        "blender": ("trackball_daemon/plugins/blender/trackball_nav/__init__.py", "return _object_center()"),
        "freecad": ("trackball_daemon/plugins/freecad/TrackballNav/tbnav_freecad.py", "center, _bb = _object_center(doc)"),
        "fusion360": ("trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py", "return _object_center(tgt)"),
        "sketchup": ("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb", "elsif zoom_mode == 'to_object'"),
        "unreal": ("trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py", "return _scene_center()"),
        "unity": ("trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs", 'zm == "to_object") return SceneCenter()'),
        "rhino": ("trackball_daemon/plugins/rhino/TrackballNav/tbnav_rhino.py", "return _document_center()"),
        "autocad": ("plugin_src/autocad/TrackballNavAcad/Plugin.cs", "_heldZoomPivot = CaptureDrawingCenter()"),
        "solidworks": ("trackball_daemon/solidworks_driver.py", "center = self._object_center(model)"),
        "onshape": ("trackball_daemon/onshape_bridge.py", "return self._object_center(conn)"),
    }
    for key, (path, token) in checks.items():
        assert "to_object" in APP_BINDING_PROFILES[key].zoom_targets
        assert token in _source(path), f"{key} advertises To Object without model-bounds behavior"


def test_zoom_dolly_selector_exists_only_where_both_paths_are_distinct():
    assert {key for key, profile in APP_BINDING_PROFILES.items() if profile.zoom_behaviors} == {
        "blender", "fusion360", "sketchup", "unreal", "unity", "rhino", "autocad"
    }
    checks = {
        "blender": "trackball_daemon/plugins/blender/trackball_nav/__init__.py",
        "fusion360": "trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py",
        "sketchup": "trackball_daemon/plugins/sketchup/trackball_nav/camera.rb",
        "unreal": "trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py",
        "unity": "trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs",
        "rhino": "trackball_daemon/plugins/rhino/TrackballNav/tbnav_rhino.py",
        "autocad": "plugin_src/autocad/TrackballNavAcad/Plugin.cs",
    }
    for key, path in checks.items():
        assert "zoom_style" in _source(path), f"{key} exposes Zoom/Dolly without consuming zoom_style"


def test_every_app_consumes_independent_orbit_and_cursor_zoom_holds():
    checks = {
        "blender": ("trackball_daemon/plugins/blender/trackball_nav/__init__.py", "orbit_hold_sec", "zoom_hold_sec"),
        "freecad": ("trackball_daemon/plugins/freecad/TrackballNav/tbnav_freecad.py", "orbit_hold_sec", "zoom_hold_sec"),
        "fusion360": ("trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py", "orbit_hold_sec", "zoom_hold_sec"),
        "sketchup": ("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb", "orbit_hold_sec", "zoom_hold_sec"),
        "unreal": ("trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py", "orbit_hold_sec", "zoom_hold_sec"),
        "unity": ("trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs", "orbit_hold_sec", "zoom_hold_sec"),
        "rhino": ("trackball_daemon/plugins/rhino/TrackballNav/tbnav_rhino.py", "orbit_hold_sec", "zoom_hold_sec"),
        "autocad": ("plugin_src/autocad/TrackballNavAcad/Plugin.cs", "orbit_hold_sec", "zoom_hold_sec"),
        "solidworks": ("trackball_daemon/solidworks_driver.py", "set_pivot_hold", "set_zoom_hold"),
        "onshape": ("trackball_daemon/onshape_bridge.py", "set_pivot_hold", "set_zoom_hold"),
    }
    assert set(checks) == set(APP_BINDING_PROFILES)
    for key, (path, orbit_token, zoom_token) in checks.items():
        source = _source(path)
        assert orbit_token in source, f"{key} exposes Pivot hold without consuming it"
        assert zoom_token in source, f"{key} exposes Zoom hold without consuming it"


def test_sketchup_selection_override_does_not_replace_to_object():
    source = _source("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb")
    assert "selection_overrides && zoom_mode == 'to_cursor'" in source
    assert "advanced.fetch('pan_scales_with_distance', true)" in source


def test_free_orbit_warns_without_overwriting_twist_action():
    source = _source("trackball_daemon/ui.py")
    assert 'Switch to "roll" for 3-axis orbit' in source
    assert 'self._set_and_save(adv + ("twist_action",), "roll")' not in source
    assert _free_orbit_needs_roll_warning("free", "turntable", "zoom") is True
    assert _free_orbit_needs_roll_warning("default", "free", "none") is True
    assert _free_orbit_needs_roll_warning("free", "free", "roll") is False
    assert _free_orbit_needs_roll_warning("turntable", "free", "zoom") is False
    assert _free_orbit_needs_roll_warning("free", "free", "zoom", supports_roll=False) is False
