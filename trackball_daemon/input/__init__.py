"""Normalized input-provider boundaries shared by keyboard and future BLE controls."""

from .aggregator import InputAggregator
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
    "InputControlDescriptor",
    "InputEvent",
    "InputPhase",
    "InputProvider",
    "InputSnapshot",
    "InputTransition",
    "ProviderHealth",
    "ProviderStatus",
)
