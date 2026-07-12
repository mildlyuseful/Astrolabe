"""UI capability declarations must match behavior already present in each host integration."""
from pathlib import Path

from trackball_daemon.binding_schema import APP_BINDING_PROFILES


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
        "godot": ("trackball_daemon/plugins/godot/trackball_nav/trackball_nav.gd",
                  'elif method == "object":\n\t\t\tpoint = _scene_center()'),
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
        "sketchup": ("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb", "when 'to_object' then object_center(model, fallback)"),
        "unreal": ("trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py", "return _scene_center()"),
        "unity": ("trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs", 'zm == "to_object") return SceneCenter()'),
        "godot": ("trackball_daemon/plugins/godot/trackball_nav/trackball_nav.gd", "return _scene_center()"),
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
        "blender", "fusion360", "sketchup"
    }
    assert "zoom_style" in _source("trackball_daemon/plugins/blender/trackball_nav/__init__.py")
    assert "zoom_style" in _source("trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py")
    assert "zoom_style" in _source("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb")


def test_sketchup_selection_override_does_not_replace_to_object():
    source = _source("trackball_daemon/plugins/sketchup/trackball_nav/camera.rb")
    assert "selection_overrides && zoom_mode == 'to_cursor'" in source
    assert "advanced.fetch('pan_scales_with_distance', true)" in source
