"""Declarative per-app binding UI contract.

The settings UI renders one ordered superstructure and each app enables only the fields and values
its integration implements. Adding a host should require one profile here, not another bespoke UI
method. Stored config/wire values remain stable; labels are user-facing presentation only.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class BindingSection:
    key: str
    title: str
    fields: tuple


@dataclass(frozen=True)
class AppBindingProfile:
    key: str
    title: str
    features: frozenset
    pivots: tuple
    orbit_styles: tuple = ("default", "free", "turntable")
    twist_actions: tuple = ("roll", "zoom", "none")
    zoom_targets: tuple = ("default", "to_center", "to_object", "to_cursor")
    zoom_behaviors: tuple = ()
    rich_actions: bool = False
    no_roll: bool = False

    def supports(self, field):
        return field in self.features


# One canonical order for every profile. Fields not enabled by an app are skipped without changing
# the relative order of everything else.
BINDING_SECTIONS = (
    BindingSection("sensitivity", "Sensitivity & rate", (
        "rate", "orbit_sensitivity", "pan_gain", "zoom_gain", "zoom_dominance", "toggle")),
    BindingSection("navigation", "Navigation mode", (
        "nav_mode", "fly_speed", "walk_speed")),
    BindingSection("orbit", "Orbit", (
        "orbit_style", "orbit_pivot", "orbit_hold", "twist_action", "lock_horizon",
        "level_horizon", "selection_override")),
    BindingSection("pan_zoom", "Pan / Zoom", (
        "zoom_target", "zoom_hold", "zoom_behavior", "pan_scales", "dynamic_clip",
        "pivot_extent")),
    BindingSection("camera", "Camera view", ("camera_lock",)),
    BindingSection("routing", "Action axes & directions", ("action_routing",)),
    BindingSection("host_extra", "Host-specific setup", ("onshape_userscript",)),
)


_BASE = frozenset({
    "rate", "orbit_sensitivity", "pan_gain", "zoom_gain", "zoom_dominance", "toggle",
    "orbit_style", "orbit_pivot", "twist_action", "level_horizon", "selection_override",
    "zoom_target", "orbit_hold", "zoom_hold", "action_routing",
})
_RICH = frozenset({"nav_mode", "fly_speed", "walk_speed", "lock_horizon", "pan_scales"})

PIVOTS_DEFAULT = ("screen_center", "cursor", "selection", "object", "origin")
PIVOTS_CAMERA = ("camera",) + PIVOTS_DEFAULT


def _profile(key, title, *, features=(), pivots=PIVOTS_DEFAULT, orbit_styles=None,
             twist_actions=None, zoom_behaviors=(), rich=False, no_roll=False, exclude=()):
    return AppBindingProfile(
        key=key,
        title=title,
        features=(_BASE | frozenset(features) | (_RICH if rich else frozenset())) - frozenset(exclude),
        pivots=tuple(pivots),
        orbit_styles=tuple(orbit_styles or ("default", "free", "turntable")),
        twist_actions=tuple(twist_actions or (("zoom", "dolly", "none") if no_roll
                                               else ("roll", "zoom", "none"))),
        zoom_behaviors=tuple(zoom_behaviors),
        rich_actions=bool(rich),
        no_roll=bool(no_roll),
    )


APP_BINDING_PROFILES = MappingProxyType({
    "blender": _profile(
        "blender", "Blender viewport navigation", rich=True,
        pivots=("camera", "screen_center", "cursor", "selection", "cursor_3d", "object", "origin"),
        twist_actions=("roll", "zoom", "dolly", "none"),
        zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior", "camera_lock")),
    "sketchup": _profile(
        "sketchup", "SketchUp model navigation", rich=True, pivots=PIVOTS_CAMERA,
        twist_actions=("roll", "zoom", "dolly", "none"),
        zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    "unreal": _profile(
        "unreal", "Unreal Editor viewport navigation", rich=True, pivots=PIVOTS_CAMERA,
        twist_actions=("roll", "zoom", "dolly", "none"),
        zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    "unity": _profile(
        "unity", "Unity Scene view navigation", rich=True, pivots=PIVOTS_CAMERA,
        twist_actions=("roll", "zoom", "dolly", "none"),
        zoom_behaviors=("zoom", "dolly"),
        features=("zoom_behavior", "dynamic_clip", "pivot_extent")),
    "godot": _profile(
        "godot", "Godot editor viewport navigation", rich=True, no_roll=True,
        pivots=PIVOTS_CAMERA, orbit_styles=("turntable",),
        features=("zoom_behavior",), twist_actions=("zoom", "dolly", "none"),
        zoom_behaviors=("zoom", "dolly"),
        exclude=("lock_horizon", "level_horizon")),
    "freecad": _profile("freecad", "FreeCAD navigation"),
    "fusion360": _profile(
        "fusion360", "Fusion 360 navigation",
        twist_actions=("roll", "zoom", "none"),
        zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    "solidworks": _profile("solidworks", "SOLIDWORKS navigation"),
    "onshape": _profile("onshape", "Onshape navigation", features=("onshape_userscript",)),
    "autocad": _profile("autocad", "AutoCAD navigation", pivots=PIVOTS_CAMERA,
                         zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    "rhino": _profile("rhino", "Rhino navigation", pivots=PIVOTS_CAMERA,
                       zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
})


def binding_profile(app_key):
    return APP_BINDING_PROFILES[app_key]
