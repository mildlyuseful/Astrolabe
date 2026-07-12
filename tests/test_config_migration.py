"""Config migrations for canonical, unambiguous orbit-pivot identifiers.

pointer -> cursor, to_pointer -> to_cursor, old cursor -> selection (Blender: cursor_3d),
and the retired legacy to_cursor (a to_center alias) -> to_center. Bindings, gains, and
everything else must pass through untouched.
"""
import json

from trackball_daemon.config import (Config, CONFIG_VERSION, DEFAULT_ACTION_AXIS_SOURCE,
                                     DEFAULT_ORBIT_PIVOT_FALLBACKS,
                                     normalize_action_axis_sources,
                                     normalize_axis_permutation,
                                     normalize_orbit_pivot_fallbacks, orbit_pivot_candidates,
                                     swap_axis_source)


def _write_v2(tmp_path, general_scheme, app_schemes):
    d = tmp_path / "TrackballDaemon"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 2,
        "general": {"scheme": general_scheme},
        "apps": {k: {"bindings": {"scheme": v}} for k, v in app_schemes.items()},
    }
    (d / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_historical_scheme_values_reach_current_names(isolated_config):
    _write_v2(
        isolated_config,
        {"orbit_pivot": "pointer", "orbit_style": "free", "zoom_mode": "to_pointer"},
        {
            "fusion360": {"orbit_pivot": "cursor", "orbit_style": "default", "zoom_mode": "to_cursor"},
            "blender": {"orbit_pivot": "cursor", "orbit_style": "default", "zoom_mode": "default"},
            "freecad": {"orbit_pivot": "view", "orbit_style": "turntable", "zoom_mode": "to_object"},
        },
    )
    cfg = Config().load()
    assert cfg.data["version"] == CONFIG_VERSION
    # general: under-mouse values take the cursor names
    assert cfg.data["general"]["scheme"]["orbit_pivot"] == "cursor"
    assert cfg.data["general"]["scheme"]["zoom_mode"] == "to_cursor"
    # old "cursor" (selection fallback) -> "selection"; legacy to_cursor -> to_center
    fus = cfg.data["apps"]["fusion360"]["bindings"]["scheme"]
    assert fus["orbit_pivot"] == "selection"
    assert fus["zoom_mode"] == "to_center"
    # Blender's old "cursor" meant its 3D cursor -> its own value
    assert cfg.data["apps"]["blender"]["bindings"]["scheme"]["orbit_pivot"] == "cursor_3d"
    # v4 disambiguates the viewport-center raycast from camera turn-in-place.
    fc = cfg.data["apps"]["freecad"]["bindings"]["scheme"]
    assert fc == {"orbit_pivot": "screen_center", "orbit_style": "turntable",
                  "zoom_mode": "to_object"}


def test_v3_migration_persists(isolated_config):
    _write_v2(isolated_config,
              {"orbit_pivot": "pointer", "orbit_style": "free", "zoom_mode": "to_cursor"}, {})
    Config().load()
    disk = json.loads((isolated_config / "TrackballDaemon" / "config.json").read_text(encoding="utf-8"))
    assert disk["version"] == CONFIG_VERSION
    assert disk["general"]["scheme"]["orbit_pivot"] == "cursor"
    assert disk["general"]["scheme"]["zoom_mode"] == "to_center"   # legacy alias retired


def test_current_version_config_untouched(isolated_config):
    d = isolated_config / "TrackballDaemon"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({
        "version": CONFIG_VERSION,
        "general": {"scheme": {"orbit_pivot": "cursor", "orbit_style": "free",
                               "zoom_mode": "to_cursor"}},
    }), encoding="utf-8")
    cfg = Config().load()
    # Current values survive a current-version load (cursor must NOT become selection).
    assert cfg.data["general"]["scheme"]["orbit_pivot"] == "cursor"
    assert cfg.data["general"]["scheme"]["zoom_mode"] == "to_cursor"


def test_removed_per_app_startup_placeholder_is_cleaned_from_current_config(isolated_config):
    d = isolated_config / "TrackballDaemon"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "config.json"
    path.write_text(json.dumps({
        "version": CONFIG_VERSION,
        "apps": {"blender": {"start_automatically": True}},
    }), encoding="utf-8")
    cfg = Config().load()
    assert "start_automatically" not in cfg.data["apps"]["blender"]
    assert "start_automatically" not in json.loads(path.read_text(encoding="utf-8"))["apps"]["blender"]


def test_fallback_chain_is_added_to_existing_config(isolated_config):
    _write_v2(isolated_config,
              {"orbit_pivot": "view", "orbit_style": "free", "zoom_mode": "to_center"}, {})
    cfg = Config().load()
    assert cfg.data["general"]["orbit_pivot_fallbacks"] == list(DEFAULT_ORBIT_PIVOT_FALLBACKS)


def test_v4_disambiguates_pivots_in_schemes_fallbacks_and_advanced(isolated_config):
    d = isolated_config / "TrackballDaemon"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({
        "version": 3,
        "general": {
            "scheme": {"orbit_pivot": "view", "orbit_style": "free", "zoom_mode": "to_center"},
            "orbit_pivot_fallbacks": ["viewpoint", "view", "object", "origin"],
        },
        "apps": {
            "blender": {
                "view_pivot_hold_sec": 0.75,
                "bindings": {"scheme": {"orbit_pivot": "viewpoint"}},
                "advanced": {"invert": {"viewpoint": {"pitch": True}}},
            },
        },
    }), encoding="utf-8")
    cfg = Config().load()
    assert cfg.data["general"]["scheme"]["orbit_pivot"] == "screen_center"
    assert cfg.data["general"]["orbit_pivot_fallbacks"] == [
        "camera", "screen_center", "object", "origin"]
    blender = cfg.data["apps"]["blender"]
    assert blender["bindings"]["scheme"]["orbit_pivot"] == "camera"
    assert blender["advanced"]["invert"]["camera"]["pitch"] is True
    assert set(blender["advanced"]["invert"]["camera"]) == {"pitch", "yaw", "roll"}
    assert blender["screen_center_pivot_hold_sec"] == 0.75
    assert "view_pivot_hold_sec" not in blender


def test_fallback_chain_normalizes_unknowns_duplicates_and_preserves_empty():
    assert normalize_orbit_pivot_fallbacks(
        ["object", "bogus", "origin", "object", 42]) == ["object", "origin"]
    assert normalize_orbit_pivot_fallbacks([]) == []
    assert normalize_orbit_pivot_fallbacks("object") == list(DEFAULT_ORBIT_PIVOT_FALLBACKS)


def test_candidates_always_restart_at_front_of_chain():
    # A failed selected method does not continue after its occurrence in the fallback chain.
    assert orbit_pivot_candidates(
        "object", ["cursor", "screen_center", "object", "origin"]) == [
            "object", "cursor", "screen_center", "origin"]


def test_legacy_pivot_names_are_normalized_at_runtime_boundaries():
    assert orbit_pivot_candidates("view", ["viewpoint", "origin"]) == [
        "screen_center", "camera", "origin"]


def test_v5_adds_identity_global_and_action_axis_routing(isolated_config):
    d = isolated_config / "TrackballDaemon"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({
        "version": 4,
        "general": {"axis_orientation": {"source": [0, 0, 7], "invert": [True]}},
        "apps": {
            "blender": {"advanced": {"axis_source": {
                "walk": {"forward": 2, "vertical": 99}}}},
            "fusion360": {"bindings": {
                "orbit": {"axis_source": [2, -1, 0]},
                "pan": {"x_src": 8, "y_src": 2},
                "zoom": {"src": "bad"},
            }},
        },
    }), encoding="utf-8")
    cfg = Config().load()
    assert cfg.data["version"] == CONFIG_VERSION == 7
    assert cfg.data["general"]["axis_orientation"] == {
        "source": [0, 1, 2], "invert": [False, False, False]}
    assert cfg.data["apps"]["blender"]["advanced"]["axis_source"]["walk"] == {
        "pitch": 0, "yaw": 1, "forward": 2, "strafe": 0, "vertical": 2}
    fusion = cfg.data["apps"]["fusion360"]["bindings"]
    assert fusion["orbit"]["axis_source"] == [2, 1, 0]
    assert (fusion["pan"]["x_src"], fusion["pan"]["y_src"], fusion["zoom"]["src"]) == (1, 2, 2)


def test_axis_normalizers_validate_global_permutation_but_allow_action_duplicates():
    assert normalize_axis_permutation([1, 0, 2]) == [1, 0, 2]
    assert normalize_axis_permutation([1, 1, 2]) == [0, 1, 2]
    assert swap_axis_source([0, 1, 2], 0, 1) == [1, 0, 2]
    assert swap_axis_source([1, 0, 2], 1, 2) == [1, 2, 0]
    routed = normalize_action_axis_sources({"walk": {"forward": 2, "vertical": 2}})
    assert routed["walk"]["forward"] == routed["walk"]["vertical"] == 2
    assert set(routed) == set(DEFAULT_ACTION_AXIS_SOURCE)
