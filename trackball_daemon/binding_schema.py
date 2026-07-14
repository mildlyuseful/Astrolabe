"""Compatibility imports for the current per-app bindings UI.

Canonical app identity and capabilities now live in :mod:`trackball_daemon.app_registry`; stable
setting metadata and presentation ordering live in :mod:`trackball_daemon.settings_schema`.
Existing consumers may keep importing this module during the Phase 1 cutover, but it owns no
independent metadata tables.
"""
from .app_registry import (
    APP_BINDING_PROFILES,
    AppBindingProfile,
    PIVOTS_CAMERA,
    PIVOTS_DEFAULT,
    binding_profile,
)
from .settings_schema import BINDING_SECTIONS, BindingSection


__all__ = (
    "APP_BINDING_PROFILES",
    "AppBindingProfile",
    "BINDING_SECTIONS",
    "BindingSection",
    "PIVOTS_CAMERA",
    "PIVOTS_DEFAULT",
    "binding_profile",
)
