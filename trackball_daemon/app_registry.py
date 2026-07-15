"""Canonical application identity, capabilities, transports, and focus resolution.

Setup implementations and their mutable detection callbacks intentionally remain in
``integrations.py``.  They reference :class:`AppSpec` records from this module so process routing,
settings capabilities, and setup cards cannot invent separate app identities.
"""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Optional


class TransportKind(str, Enum):
    """Navigation delivery boundary owned by an application."""

    BROKER = "broker"
    SOLIDWORKS_COM = "solidworks_com"
    ONSHAPE_BRIDGE = "onshape_bridge"


class FocusKind(str, Enum):
    """How a foreground process becomes an application context."""

    DESKTOP_PROCESS = "desktop_process"
    ONSHAPE_BROWSER = "onshape_browser"


class OnshapeFocusState(str, Enum):
    """Connection and foreground state kept distinct for browser-hosted Onshape."""

    DISCONNECTED = "disconnected"
    CONNECTED_BACKGROUND = "connected_background"
    CONNECTED_FOREGROUND = "connected_foreground"


@dataclass(frozen=True)
class ProcessSelector:
    """Case-insensitive executable-name substring retained from the current router contract."""

    contains: str

    def __post_init__(self):
        normalized = self.contains.strip().lower()
        if not normalized or normalized != self.contains:
            raise ValueError("process selector must be a non-empty normalized lowercase string")

    def matches(self, process_name: Optional[str]) -> bool:
        return bool(process_name and self.contains in _normalize_process_name(process_name))


@dataclass(frozen=True)
class AppBindingProfile:
    """Current per-app navigation capability and option contract.

    This type keeps the established UI-facing API while the record itself is now nested under the
    canonical :class:`AppSpec` rather than owned by ``binding_schema.py``.
    """

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


@dataclass(frozen=True)
class AppSpec:
    """Immutable, stable identity and runtime capabilities for one supported app."""

    app_id: str
    display_name: str
    process_selectors: tuple
    supported_modes: tuple
    capabilities: frozenset
    transport: TransportKind
    focus_kind: FocusKind
    binding_profile: AppBindingProfile

    def __post_init__(self):
        if not self.app_id or self.app_id != self.app_id.strip().lower():
            raise ValueError("app_id must be a non-empty normalized lowercase string")
        if self.binding_profile.key != self.app_id:
            raise ValueError("binding profile identity must match app identity")
        if not self.process_selectors:
            raise ValueError(f"{self.app_id} must declare at least one process selector")
        if not self.supported_modes or self.supported_modes[0] != "orbit":
            raise ValueError(f"{self.app_id} must support orbit as its base navigation mode")

    @property
    def key(self):
        """Compatibility spelling used by existing integration/config consumers."""
        return self.app_id

    @property
    def name(self):
        """Compatibility spelling used by existing setup-card consumers."""
        return self.display_name

    def matches_process(self, process_name: Optional[str]) -> bool:
        return any(selector.matches(process_name) for selector in self.process_selectors)


@dataclass(frozen=True)
class ForegroundContext:
    """One process snapshot and its resolved app, including explicit Onshape bridge state."""

    process_name: Optional[str]
    app_id: Optional[str]
    focus_kind: Optional[FocusKind]
    onshape_state: OnshapeFocusState


_BASE_FEATURES = frozenset({
    "rate", "orbit_sensitivity", "pan_gain", "zoom_gain", "zoom_dominance",
    "orbit_style", "orbit_pivot", "twist_action", "level_horizon", "selection_override",
    "zoom_target", "orbit_hold", "zoom_hold", "action_routing",
})
_RICH_FEATURES = frozenset({"nav_mode", "fly_speed", "walk_speed", "lock_horizon", "pan_scales"})

PIVOTS_DEFAULT = ("screen_center", "cursor", "selection", "object", "origin")
PIVOTS_CAMERA = ("camera",) + PIVOTS_DEFAULT
_BROWSER_PROCESS_NAMES = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")


def _selectors(*values):
    return tuple(ProcessSelector(value) for value in values)


def _profile(app_id, title, *, features=(), pivots=PIVOTS_DEFAULT, orbit_styles=None,
             twist_actions=None, zoom_behaviors=(), rich=False, no_roll=False, exclude=()):
    return AppBindingProfile(
        key=app_id,
        title=title,
        features=(_BASE_FEATURES | frozenset(features) |
                  (_RICH_FEATURES if rich else frozenset())) - frozenset(exclude),
        pivots=tuple(pivots),
        orbit_styles=tuple(orbit_styles or ("default", "free", "turntable")),
        twist_actions=tuple(twist_actions or (("zoom", "dolly", "none") if no_roll
                                               else ("roll", "zoom", "none"))),
        zoom_behaviors=tuple(zoom_behaviors),
        rich_actions=bool(rich),
        no_roll=bool(no_roll),
    )


def _spec(app_id, display_name, process_names, title, *, transport=TransportKind.BROKER,
          focus_kind=FocusKind.DESKTOP_PROCESS, rich=False, no_roll=False, **profile_kwargs):
    profile = _profile(app_id, title, rich=rich, no_roll=no_roll, **profile_kwargs)
    capabilities = set(profile.features)
    capabilities.add("rich_actions" if rich else "lean_actions")
    if not no_roll:
        capabilities.add("roll")
    return AppSpec(
        app_id=app_id,
        display_name=display_name,
        process_selectors=_selectors(*process_names),
        supported_modes=("orbit", "fly", "walk") if rich else ("orbit",),
        capabilities=frozenset(capabilities),
        transport=transport,
        focus_kind=focus_kind,
        binding_profile=profile,
    )


# This tuple is the only code-owned supported-app identity/order table. Packaged profile files are
# validated against it by config.py; integration setup records below reference these exact objects.
APP_SPECS = (
    _spec("blender", "Blender", ("blender",), "Blender viewport navigation", rich=True,
          pivots=("camera", "screen_center", "cursor", "selection", "cursor_3d", "object", "origin"),
          twist_actions=("roll", "zoom", "dolly", "none"),
          zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior", "camera_lock")),
    _spec("freecad", "FreeCAD", ("freecad",), "FreeCAD navigation"),
    _spec("sketchup", "SketchUp", ("sketchup",), "SketchUp model navigation", rich=True,
          pivots=PIVOTS_CAMERA, twist_actions=("roll", "zoom", "dolly", "none"),
          zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    _spec("unreal", "Unreal Engine", ("unrealeditor", "ue4editor"),
          "Unreal Editor viewport navigation", rich=True, pivots=PIVOTS_CAMERA,
          twist_actions=("roll", "zoom", "dolly", "none"),
          zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    _spec("unity", "Unity", ("unity",), "Unity Scene view navigation", rich=True,
          pivots=PIVOTS_CAMERA, twist_actions=("roll", "zoom", "dolly", "none"),
          zoom_behaviors=("zoom", "dolly"),
          features=("zoom_behavior", "dynamic_clip", "pivot_extent")),
    _spec("godot", "Godot", ("godot",), "Godot editor viewport navigation", rich=True,
          no_roll=True, pivots=PIVOTS_CAMERA, orbit_styles=("turntable",),
          features=("zoom_behavior",), twist_actions=("zoom", "dolly", "none"),
          zoom_behaviors=("zoom", "dolly"), exclude=("lock_horizon", "level_horizon")),
    _spec("rhino", "Rhino", ("rhino",), "Rhino navigation", pivots=PIVOTS_CAMERA,
          zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
    _spec("fusion360", "Fusion 360", ("fusion",), "Fusion 360 navigation",
          twist_actions=("roll", "zoom", "none"), zoom_behaviors=("zoom", "dolly"),
          features=("zoom_behavior",)),
    _spec("solidworks", "SolidWorks", ("sldworks",), "SOLIDWORKS navigation",
          transport=TransportKind.SOLIDWORKS_COM),
    _spec("onshape", "Onshape", _BROWSER_PROCESS_NAMES, "Onshape navigation",
          transport=TransportKind.ONSHAPE_BRIDGE, focus_kind=FocusKind.ONSHAPE_BROWSER,
          features=("onshape_userscript",)),
    _spec("autocad", "AutoCAD", ("acad",), "AutoCAD navigation", pivots=PIVOTS_CAMERA,
          zoom_behaviors=("zoom", "dolly"), features=("zoom_behavior",)),
)

APP_SPECS_BY_ID = MappingProxyType({spec.app_id: spec for spec in APP_SPECS})
APP_IDS = tuple(spec.app_id for spec in APP_SPECS)

# Generated compatibility view for existing UI/host tests. The data remains owned by APP_SPECS.
APP_BINDING_PROFILES = MappingProxyType(
    {spec.app_id: spec.binding_profile for spec in APP_SPECS})


def app_spec(app_id):
    return APP_SPECS_BY_ID[app_id]


def binding_profile(app_id):
    return APP_SPECS_BY_ID[app_id].binding_profile


def _normalize_process_name(process_name):
    if not process_name:
        return None
    return str(process_name).replace("/", "\\").rsplit("\\", 1)[-1].lower()


def resolve_foreground_context(process_name, *, onshape_connected=False):
    """Resolve one process snapshot without conflating Onshape connection with foreground.

    A connected bridge in a background browser remains ``CONNECTED_BACKGROUND``. Only the
    combination of a foreground browser selector and a connected bridge resolves to Onshape.
    The bridge's own viewport-focus signal remains the downstream fine gate.
    """
    process_name = _normalize_process_name(process_name)
    onshape_state = (OnshapeFocusState.CONNECTED_BACKGROUND if onshape_connected
                     else OnshapeFocusState.DISCONNECTED)
    if not process_name:
        return ForegroundContext(None, None, None, onshape_state)

    for spec in APP_SPECS:
        if spec.focus_kind is FocusKind.DESKTOP_PROCESS and spec.matches_process(process_name):
            return ForegroundContext(process_name, spec.app_id, spec.focus_kind, onshape_state)

    onshape = APP_SPECS_BY_ID["onshape"]
    if onshape.matches_process(process_name) and onshape_connected:
        return ForegroundContext(process_name, "onshape", FocusKind.ONSHAPE_BROWSER,
                                 OnshapeFocusState.CONNECTED_FOREGROUND)
    return ForegroundContext(process_name, None, None, onshape_state)
