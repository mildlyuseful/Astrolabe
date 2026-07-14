"""One target-aware boundary for socket, SolidWorks, and Onshape navigation delivery."""
from dataclasses import dataclass
from collections.abc import Mapping
import math
import threading

from .app_registry import APP_SPECS_BY_ID, TransportKind


def _profile_signature(value):
    """Detach mutable config containers while preserving stable equality semantics."""
    if isinstance(value, Mapping):
        return tuple(sorted((key, _profile_signature(child)) for key, child in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_profile_signature(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_profile_signature(child) for child in value))
    return value


@dataclass(frozen=True)
class NavigationEnvelope:
    target_app: str
    orbit: tuple
    pan: tuple
    zoom: float
    state_revision: int

    def __post_init__(self):
        if self.target_app not in APP_SPECS_BY_ID:
            raise ValueError(f"unknown navigation target: {self.target_app}")
        try:
            orbit = tuple(float(value) for value in self.orbit)
            pan = tuple(float(value) for value in self.pan)
            zoom = float(self.zoom)
        except (TypeError, ValueError) as exc:
            raise ValueError("navigation envelope values must be numeric") from exc
        if len(orbit) != 3 or len(pan) != 2:
            raise ValueError("navigation envelope requires three orbit and two pan values")
        if not all(math.isfinite(value) for value in orbit + pan + (zoom,)):
            raise ValueError("navigation envelope values must be finite")
        if type(self.state_revision) is not int or self.state_revision < 0:
            raise ValueError("navigation state revision must be a non-negative int")
        object.__setattr__(self, "orbit", orbit)
        object.__setattr__(self, "pan", pan)
        object.__setattr__(self, "zoom", zoom)


class NavigationRouter:
    """Serialize target switches and route each envelope to exactly one transport."""

    def __init__(self, broker, solidworks_driver, onshape_bridge):
        self._broker = broker
        self._direct = {
            "solidworks": solidworks_driver,
            "onshape": onshape_bridge,
        }
        self._lock = threading.RLock()
        self._active_target = None
        self._state_revisions = {}
        self._profile_revisions = {}
        self._profiles = {}

    @property
    def active_target(self):
        with self._lock:
            return self._active_target

    @staticmethod
    def _transport(target):
        return APP_SPECS_BY_ID[target].transport

    def _discard_unlocked(self, target):
        if target is None:
            return
        if self._transport(target) is TransportKind.BROKER:
            self._broker.discard_pending(target)
        else:
            self._direct[target].discard_pending()

    def activate(self, target):
        if target is not None and target not in APP_SPECS_BY_ID:
            raise ValueError(f"unknown navigation target: {target}")
        with self._lock:
            if target == self._active_target:
                return
            old = self._active_target
            self._discard_unlocked(old)
            self._active_target = target
            broker_target = (target if target is not None and
                             self._transport(target) is TransportKind.BROKER else None)
            self._broker.activate_target(broker_target)
            if target is not None:
                self._discard_unlocked(target)

    def set_rate(self, target, hz):
        if self._transport(target) is TransportKind.BROKER:
            self._broker.set_rate(target, hz)
        else:
            self._direct[target].set_rate(hz)

    def set_scheme(self, target, *, orbit_pivot, orbit_style, zoom_mode, advanced=None,
                   selection_overrides_pivot=True, orbit_pivot_fallbacks=(),
                   level_horizon_on_entry=True, pivot_hold_sec=0.5, zoom_hold_sec=0.5):
        profile = _profile_signature({
            "orbit_pivot": orbit_pivot,
            "orbit_style": orbit_style,
            "zoom_mode": zoom_mode,
            "advanced": advanced,
            "selection_overrides_pivot": bool(selection_overrides_pivot),
            "orbit_pivot_fallbacks": tuple(orbit_pivot_fallbacks),
            "level_horizon_on_entry": bool(level_horizon_on_entry),
            "pivot_hold_sec": float(pivot_hold_sec),
            "zoom_hold_sec": float(zoom_hold_sec),
        })
        with self._lock:
            if self._profiles.get(target) == profile:
                return self._profile_revisions.get(target, 0)
            self._discard_unlocked(target)
            revision = self._profile_revisions.get(target, 0) + 1
            self._profile_revisions[target] = revision
            self._profiles[target] = profile
            if self._transport(target) is TransportKind.BROKER:
                self._broker.set_scheme(
                    target, orbit_pivot, orbit_style, zoom_mode, advanced=advanced,
                    profile_revision=revision)
            else:
                driver = self._direct[target]
                driver.set_scheme(
                    orbit_pivot, orbit_style, zoom_mode,
                    selection_overrides_pivot=selection_overrides_pivot,
                    orbit_pivot_fallbacks=list(orbit_pivot_fallbacks),
                    level_horizon_on_entry=level_horizon_on_entry)
                driver.set_pivot_hold(pivot_hold_sec)
                driver.set_zoom_hold(zoom_hold_sec)
            return revision

    def submit(self, envelope):
        if not isinstance(envelope, NavigationEnvelope):
            raise TypeError("navigation submit requires a NavigationEnvelope")
        target = envelope.target_app
        with self._lock:
            if target != self._active_target:
                return False
            previous = self._state_revisions.get(target, -1)
            if envelope.state_revision < previous:
                return False
            if envelope.state_revision > previous:
                if previous >= 0:
                    self._discard_unlocked(target)
                self._state_revisions[target] = envelope.state_revision
            ox, oy, oz = envelope.orbit
            px, py = envelope.pan
            if self._transport(target) is TransportKind.BROKER:
                return self._broker.submit(
                    target, ox, oy, oz, px, py, envelope.zoom,
                    state_revision=envelope.state_revision)
            self._direct[target].submit(ox, oy, oz, px, py, envelope.zoom)
            return True

    def delivery_state(self, target):
        with self._lock:
            state = {
                "active": target == self._active_target,
                "state_revision": self._state_revisions.get(target, -1),
                "profile_revision": self._profile_revisions.get(target, 0),
            }
            if self._transport(target) is TransportKind.BROKER:
                state.update(self._broker.delivery_state(target))
            return state
