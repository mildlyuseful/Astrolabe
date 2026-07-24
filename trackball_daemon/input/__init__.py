# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Normalized input-provider boundaries shared by keyboard and future BLE controls."""

from .aggregator import InputAggregator
from .bindings import (
    BindingController,
    BindingContext,
    BindingDefinition,
    BindingDiagnostic,
    BindingProfileCatalog,
    CompiledBindingProfile,
    PointerButtonSink,
    SystemBindingProfile,
    compile_binding_profile,
    compile_binding_rows,
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
    "BindingController",
    "BindingContext",
    "BindingDefinition",
    "BindingDiagnostic",
    "BindingProfileCatalog",
    "CompiledBindingProfile",
    "InputControlDescriptor",
    "InputEvent",
    "InputPhase",
    "InputProvider",
    "InputSnapshot",
    "InputTransition",
    "ProviderHealth",
    "ProviderStatus",
    "PointerButtonSink",
    "SystemBindingProfile",
    "compile_binding_profile",
    "compile_binding_rows",
    "compose_binding_profile",
    "load_system_binding_profiles",
    "validate_keybinding_override_suite",
)
