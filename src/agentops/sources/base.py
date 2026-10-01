"""The `EvidenceSource` protocol every adapter implements, and the values it reads and writes.

The graph only ever sees this interface, so it cannot tell a simulator from a real system.
"""

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SIGNAL_ID_PATTERN = r"^[a-z0-9_-]+$"
ConfigValue = str | int | float | bool


@runtime_checkable
class EvidenceSource(Protocol):
    def get_logs(self, service: str, window: "Window", query: str | None) -> list["LogLine"]: ...

    def get_metrics(self, service: str, names: list[str], window: "Window") -> list["Series"]: ...

    def get_deployments(self, service: str, window: "Window") -> list["Deployment"]: ...

    def get_config(self, service: str) -> dict[str, ConfigValue]: ...

    def get_dependency_health(self, service: str) -> list["DependencyStatus"]: ...

    def apply(self, action: "ActionRequest", idempotency_key: str) -> "ActionResult": ...

    def ledger(self) -> list["AppliedAction"]: ...


class SourceValue(BaseModel):
    """Base for every value crossing the source boundary: unknown keys are a typo, not data."""

    model_config = ConfigDict(extra="forbid")


class Window(SourceValue):
    """A closed time interval; both ends are included."""

    start: AwareDatetime
    end: AwareDatetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


class LogLine(SourceValue):
    id: str = Field(pattern=SIGNAL_ID_PATTERN)
    timestamp: AwareDatetime
    level: str
    message: str
    service: str


class Point(SourceValue):
    at: AwareDatetime
    value: float


class Series(SourceValue):
    id: str = Field(pattern=SIGNAL_ID_PATTERN)
    name: str
    unit: str
    points: list[Point]


class Deployment(SourceValue):
    id: str = Field(pattern=SIGNAL_ID_PATTERN)
    version: str
    previous_version: str
    deployed_at: AwareDatetime
    author: str
    diff_summary: str


class DependencyStatus(SourceValue):
    id: str = Field(pattern=SIGNAL_ID_PATTERN)
    name: str
    status: Literal["healthy", "degraded", "down"]
    detail: str


class ActionRequest(SourceValue):
    action: str
    target: str
    params: dict[str, Any] = Field(default_factory=dict)


class ActionResult(SourceValue):
    """The outcome of `apply`. A refusal is `ok=False` with a sentence in `detail`."""

    ok: bool
    applied: bool
    idempotency_key: str
    detail: str


class AppliedAction(SourceValue):
    idempotency_key: str
    action: str
    target: str
    params: dict[str, Any]
