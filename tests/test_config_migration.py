"""The v2 -> v3 config migration: scheme values renamed to match the UI labels.

pointer -> cursor, to_pointer -> to_cursor, old cursor -> selection (Blender: cursor_3d),
and the retired legacy to_cursor (a to_center alias) -> to_center. Bindings, gains, and
everything else must pass through untouched.
"""
import json

from trackball_daemon.config import (Config, CONFIG_VERSION, DEFAULT_ORBIT_PIVOT_FALLBACKS,
                                     normalize_orbit_pivot_fallbacks, orbit_pivot_candidates)


def _write_v2(tmp_path, general_scheme, app_schemes):
    d = tmp_path / "TrackballDaemon"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 2,
        "general": {"scheme": general_scheme},
        "apps": {k: {"bindings": {"scheme": v}} for k, v in app_schemes.items()},
    }
    (d / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_v3_renames_scheme_values(isolated_config):
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
    # untouched values pass through
    fc = cfg.data["apps"]["freecad"]["bindings"]["scheme"]
    assert fc == {"orbit_pivot": "view", "orbit_style": "turntable", "zoom_mode": "to_object"}


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
    # v3 values survive a v3 load (no double-migration: cursor must NOT become selection)
    assert cfg.data["general"]["scheme"]["orbit_pivot"] == "cursor"
    assert cfg.data["general"]["scheme"]["zoom_mode"] == "to_cursor"


def test_fallback_chain_is_added_to_existing_config(isolated_config):
    _write_v2(isolated_config,
              {"orbit_pivot": "view", "orbit_style": "free", "zoom_mode": "to_center"}, {})
    cfg = Config().load()
    assert cfg.data["general"]["orbit_pivot_fallbacks"] == list(DEFAULT_ORBIT_PIVOT_FALLBACKS)


def test_fallback_chain_normalizes_unknowns_duplicates_and_preserves_empty():
    assert normalize_orbit_pivot_fallbacks(
        ["object", "bogus", "origin", "object", 42]) == ["object", "origin"]
    assert normalize_orbit_pivot_fallbacks([]) == []
    assert normalize_orbit_pivot_fallbacks("object") == list(DEFAULT_ORBIT_PIVOT_FALLBACKS)


def test_candidates_always_restart_at_front_of_chain():
    # A failed selected method does not continue after its occurrence in the fallback chain.
    assert orbit_pivot_candidates("object", ["cursor", "view", "object", "origin"]) == [
        "object", "cursor", "view", "origin"]
