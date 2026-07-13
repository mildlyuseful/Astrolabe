"""Blender/SketchUp/Unreal nav wiring: additive broker "adv" pass-through, config `advanced`
blocks, and App._apply_schemes attaching the FOCUSED broker app's advanced.

The invariant under test: extending the broker must NOT change what existing add-ins (Fusion) see —
they read only o/p/z/op/os/zm, so "adv" is present only when the focused app sets it.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from trackball_daemon.app import App
from trackball_daemon.config import (Config, _DEFAULT_BLENDER_ADVANCED, _DEFAULT_SKETCHUP_ADVANCED,
                                     compose_advanced_with_host_baseline, host_baseline_payload)
from trackball_daemon.navbroker import NavBroker
from trackball_daemon.ui import _PIVOT_LABELS


# --- broker frame construction --------------------------------------------------------
def test_frame_omits_adv_when_unset():
    # Fusion-style scheme (no advanced) -> frame is exactly the legacy shape, no "adv" key.
    scheme = {"op": "screen_center", "os": "free", "zm": "to_center", "adv": None}
    frame = NavBroker._build_frame([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], scheme)
    assert frame == {"o": [1.0, 2.0, 3.0], "p": [4.0, 5.0], "z": 6.0,
                     "op": "screen_center", "os": "free", "zm": "to_center"}
    assert "adv" not in frame
    json.dumps(frame)                          # must be serialisable


def test_frame_includes_adv_when_set():
    adv = {"nav_mode": "fly", "twist_action": "roll"}
    scheme = {"op": "camera", "os": "turntable", "zm": "to_center", "adv": adv}
    frame = NavBroker._build_frame([0.0] * 6, scheme)
    assert frame["adv"] == adv
    assert frame["op"] == "camera" and frame["os"] == "turntable"


def test_set_scheme_stores_advanced():
    b = NavBroker(47999)
    b.set_scheme("screen_center", "free", "to_center")  # default: no advanced
    assert b._scheme["adv"] is None
    adv = {"nav_mode": "walk"}
    b.set_scheme("camera", "turntable", "to_object", advanced=adv)
    assert b._scheme["adv"] == adv
    # legacy 3-arg call site still works unchanged
    b.set_scheme("object", "free", "to_cursor")
    assert b._scheme == {"op": "object", "os": "free", "zm": "to_cursor", "adv": None}


# --- config: the additive Blender advanced block --------------------------------------
def test_blender_app_has_advanced_block(isolated_config):
    cfg = Config().load()
    blender = cfg.data["apps"]["blender"]
    assert "advanced" in blender
    assert set(blender["advanced"]) == set(_DEFAULT_BLENDER_ADVANCED)
    # Blender's native default pivot is "camera" (orbit about view_location)
    assert blender["bindings"]["scheme"]["orbit_pivot"] == "camera"
    # Every app now has the shared Twist action; Fusion additionally supports zoom vs dolly.
    assert cfg.data["apps"]["fusion360"]["advanced"] == {
        "twist_action": "roll", "zoom_style": "zoom"}


def test_blender_bundled_version_markers_stay_in_sync():
    root = Path(__file__).parents[1] / "trackball_daemon/plugins/blender/trackball_nav"
    assert json.loads((root / "version.json").read_text(encoding="utf-8"))["version"] == "0.1.21"
    source = (root / "__init__.py").read_text(encoding="utf-8")
    assert 'ADDIN_VERSION = "0.1.21"' in source
    assert '"version": (0, 1, 21)' in source


def test_blender_addon_consumes_shared_zoom_target_and_behavior():
    source = (Path(__file__).parents[1] /
              "trackball_daemon/plugins/blender/trackball_nav/__init__.py").read_text(
                  encoding="utf-8")
    assert "def _zoom_pivot(" in source
    assert 'frame.get("zm", "to_center")' in source
    assert 'adv.get("zoom_style", "zoom")' in source
    assert "_dolly(rv, z, pivot)" in source


def test_advanced_appears_on_old_config_via_deep_merge(isolated_config):
    # Simulate a pre-existing v2 config that predates the advanced block.
    import copy
    from trackball_daemon import config as cfgmod
    disk = copy.deepcopy(cfgmod.DEFAULTS)
    disk["apps"]["blender"].pop("advanced", None)
    path = isolated_config / "config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk, f)
    cfg = Config().load()
    assert cfg.data["apps"]["blender"]["advanced"] == _DEFAULT_BLENDER_ADVANCED  # merged back in


def test_sketchup_app_has_blender_parity_advanced_block(isolated_config):
    cfg = Config().load()
    sketchup = cfg.data["apps"]["sketchup"]
    assert sketchup["advanced"] == _DEFAULT_SKETCHUP_ADVANCED
    assert sketchup["advanced"]["nav_mode"] == "orbit"
    assert set(sketchup["advanced"]["invert"]) == {"orbit", "camera", "fly", "walk"}
    assert set(sketchup["advanced"]["invert"]["fly"]) == {
        "pitch", "yaw", "bank", "forward", "strafe", "vertical"}
    assert set(sketchup["advanced"]["invert"]["walk"]) == {
        "pitch", "yaw", "forward", "strafe", "vertical"}
    assert sketchup["advanced"]["axis_source"]["walk"]["forward"] == 1
    assert sketchup["advanced"]["axis_source"]["walk"]["vertical"] == 2


def test_sketchup_advanced_appears_on_old_config_via_deep_merge(isolated_config):
    import copy
    from trackball_daemon import config as cfgmod
    disk = copy.deepcopy(cfgmod.DEFAULTS)
    disk["apps"]["sketchup"].pop("advanced", None)
    path = isolated_config / "config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk, f)
    cfg = Config().load()
    assert cfg.data["apps"]["sketchup"]["advanced"] == _DEFAULT_SKETCHUP_ADVANCED


# --- App._apply_schemes: advanced only for a focused Blender ---------------------------
def _app_with_real_config(cfg, engine_app):
    app = App.__new__(App)
    app.config = cfg
    app._engine_app = engine_app
    app._last_scheme_pushed = None
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    app.sw_driver = None
    app.onshape_bridge = None
    app.broker = SimpleNamespace(schemes=[])
    app.broker.set_scheme = lambda **kw: app.broker.schemes.append(kw)
    return app


def test_blender_focus_sends_advanced(isolated_config):
    cfg = Config().load()
    app = _app_with_real_config(cfg, "blender")
    app._apply_schemes()
    sent = app.broker.schemes[-1]
    adv = sent["advanced"]
    # Core Blender advanced plus immutable host corrections and app-root settings.
    for k, v in cfg.data["apps"]["blender"]["advanced"].items():
        if k != "invert":
            assert adv[k] == v
    expected = compose_advanced_with_host_baseline(
        "blender", cfg.data["apps"]["blender"]["advanced"])
    assert adv["invert"] == expected["invert"]
    assert adv["host_baseline"] == host_baseline_payload("blender")
    assert adv["selection_overrides_pivot"] is True
    assert adv["level_horizon_on_entry"] is True
    assert {"orbit_pivot", "orbit_style", "zoom_mode"} <= set(sent)


def test_fusion_focus_sends_selection_override_only(isolated_config):
    # Fusion has no richer advanced block. Shared baseline/pivot metadata is still folded into adv;
    # Fusion ignores unknown adv keys because its correction was already applied in the daemon.
    cfg = Config().load()
    app = _app_with_real_config(cfg, "fusion360")
    app._apply_schemes()
    sent = app.broker.schemes[-1]
    assert sent["advanced"]["selection_overrides_pivot"] is True
    assert sent["advanced"]["level_horizon_on_entry"] is True
    assert sent["advanced"]["host_baseline"] == host_baseline_payload("fusion360")
    assert sent["advanced"]["orbit_pivot_fallbacks"] == [
        "cursor_3d", "camera", "object", "origin"]
    assert sent["advanced"]["orbit_pivot_candidates"] == [
        "screen_center", "cursor_3d", "camera", "object", "origin"]
    assert {"orbit_pivot", "orbit_style", "zoom_mode"} <= set(sent)


def test_unreal_focus_sends_its_advanced(isolated_config):
    # Unreal has its own advanced block (orbit/fly/walk, twist action, lock-horizon, per-mode inverts);
    # focusing Unreal attaches Unreal's advanced, not Blender's, with selection_overrides_pivot folded.
    cfg = Config().load()
    app = _app_with_real_config(cfg, "unreal")
    app._apply_schemes()
    sent = app.broker.schemes[-1]
    adv = sent["advanced"]
    assert adv["nav_mode"] == "orbit"
    assert adv["selection_overrides_pivot"] is True
    assert adv["nav_mode"] == cfg.data["apps"]["unreal"]["advanced"]["nav_mode"]
    assert adv is not cfg.data["apps"]["blender"]["advanced"]


def test_per_app_level_horizon_override_is_sent(isolated_config):
    cfg = Config().load()
    cfg.data["apps"]["blender"]["level_horizon_on_entry"] = False
    app = _app_with_real_config(cfg, "blender")
    app._apply_schemes()
    assert app.broker.schemes[-1]["advanced"]["level_horizon_on_entry"] is False


def test_sketchup_focus_sends_its_advanced(isolated_config):
    cfg = Config().load()
    app = _app_with_real_config(cfg, "sketchup")
    app._apply_schemes()
    sent = app.broker.schemes[-1]
    adv = sent["advanced"]
    assert adv["nav_mode"] == "orbit"
    assert adv["selection_overrides_pivot"] is True
    assert sent["advanced"] is not cfg.data["apps"]["blender"]["advanced"]


def test_blender_addon_consumes_selection_override():
    source = (Path(__file__).parents[1] / "trackball_daemon" / "plugins" / "blender" /
              "trackball_nav" / "__init__.py").read_text(encoding="utf-8")
    assert 'adv.get("selection_overrides_pivot", True)' in source
    assert 'if sel_override and op != "camera":' in source
    assert "selected = _selection_median()" in source


def test_every_socket_integration_consumes_expanded_pivot_candidates():
    root = Path(__file__).parents[1]
    sources = [
        root / "trackball_daemon/plugins/blender/trackball_nav/__init__.py",
        root / "trackball_daemon/plugins/freecad/TrackballNav/tbnav_freecad.py",
        root / "trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py",
        root / "trackball_daemon/plugins/godot/trackball_nav/trackball_nav.gd",
        root / "trackball_daemon/plugins/rhino/TrackballNav/tbnav_rhino.py",
        root / "trackball_daemon/plugins/sketchup/trackball_nav/camera.rb",
        root / "trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs",
        root / "trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py",
        root / "plugin_src/autocad/TrackballNavAcad/Plugin.cs",
    ]
    for source in sources:
        assert "orbit_pivot_candidates" in source.read_text(encoding="utf-8"), source


def test_every_rich_integration_consumes_per_action_axis_sources():
    root = Path(__file__).parents[1]
    sources = [
        root / "trackball_daemon/plugins/blender/trackball_nav/__init__.py",
        root / "trackball_daemon/plugins/godot/trackball_nav/trackball_nav.gd",
        root / "trackball_daemon/plugins/sketchup/trackball_nav/camera.rb",
        root / "trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs",
        root / "trackball_daemon/plugins/unreal/TrackballNav/Content/Python/trackball_nav.py",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "axis_source" in text, source
        assert "ActionRouting" in text or "action_routing" in text, source


def test_ui_exposes_global_and_per_action_axis_routing():
    source = (Path(__file__).parents[1] / "trackball_daemon/ui.py").read_text(encoding="utf-8")
    schema = (Path(__file__).parents[1] / "trackball_daemon/binding_schema.py").read_text(
        encoding="utf-8")
    assert "Physical trackball orientation" in source
    assert "Changing a source swaps axes instead of duplicating one" in source
    assert "Action axes & directions" in schema
    assert '"action_routing"' in schema


def test_pivot_ui_uses_one_canonical_case_sensitive_vocabulary():
    assert _PIVOT_LABELS == {
        "camera": "Camera",
        "screen_center": "Screen Center",
        "cursor": "Under Cursor (mouse)",
        "selection": "Selection",
        "cursor_3d": "3D Cursor",
        "object": "Model Center",
        "origin": "World Origin",
    }
    source = (Path(__file__).parents[1] / "trackball_daemon/ui.py").read_text(encoding="utf-8")
    assert "Auto Depth" not in source and "Viewpoint" not in source
    assert '("Selection", "object")' not in source
