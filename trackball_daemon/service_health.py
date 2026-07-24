"""Immutable runtime health values shared by navigation transport owners and the UI."""

from dataclasses import dataclass
from enum import Enum


class ServiceHealthState(str, Enum):
    """Operational states exposed for long-lived daemon services."""

    DISABLED = "disabled"
    WAITING = "waiting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class ServiceHealth:
    """One detached health observation with a user-actionable current detail."""

    service_id: str
    state: ServiceHealthState
    detail: str

    def __post_init__(self):
        service_id = str(self.service_id).strip()
        detail = str(self.detail).strip()
        if not service_id:
            raise ValueError("service health requires a service ID")
        if not detail:
            raise ValueError("service health requires a detail")
        object.__setattr__(self, "service_id", service_id)
        object.__setattr__(self, "state", ServiceHealthState(self.state))
        object.__setattr__(self, "detail", detail)
