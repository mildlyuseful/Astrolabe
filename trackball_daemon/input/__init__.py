"""Normalized input-provider boundaries shared by keyboard and future BLE controls."""

from .aggregator import InputAggregator
from .bindings import (
    BindingContext,
    BindingDefinition,
    BindingProfileCatalog,
    SystemBindingProfile,
    compose_binding_profile,
    load_system_binding_profiles,
    validate_keybinding_override_suite,
)
from .model import (
    InputControlDescriptor,
    InputEvent,
    InputPhase,
    InputProvider,
    InputSnapshot,
    InputTransition,
    ProviderHealth,
    ProviderStatus,
)

__all__ = (
    "InputAggregator",
    "BindingContext",
    "BindingDefinition",
    "BindingProfileCatalog",
    "InputControlDescriptor",
    "InputEvent",
    "InputPhase",
    "InputProvider",
    "InputSnapshot",
    "InputTransition",
    "ProviderHealth",
    "ProviderStatus",
    "SystemBindingProfile",
    "compose_binding_profile",
    "load_system_binding_profiles",
    "validate_keybinding_override_suite",
)
