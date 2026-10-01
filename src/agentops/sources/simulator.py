"""`SimulatorSource`: an `EvidenceSource` that serves one scenario's signals from memory.

It holds its own deep copy of the signals, so applying an action changes this source only,
never the loaded `Scenario` or another source built from it. Every read returns copies for the
same reason. The grading key and scripted outputs have no accessor here.
"""

from agentops.sources.base import (
    ActionRequest,
    ActionResult,
    AppliedAction,
    ConfigValue,
    DependencyStatus,
    Deployment,
    LogLine,
    Series,
    Window,
)
from agentops.sources.scenario import PostActionWorld, Scenario, Signals


class SimulatorSource:
    def __init__(self, scenario: Scenario) -> None:
        self._service = scenario.service
        self._signals: Signals = scenario.signals.model_copy(deep=True)
        self._worlds: dict[str, PostActionWorld] = {
            action: world.model_copy(deep=True)
            for action, world in scenario.post_action_world.items()
        }
        self._ledger: list[AppliedAction] = []

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        """Log lines for `service` inside `window`; `query` is a case-insensitive substring."""
        needle = query.casefold() if query else None
        return [
            line.model_copy(deep=True)
            for line in self._signals.logs
            if line.service == service
            and window.contains(line.timestamp)
            and (needle is None or needle in line.message.casefold())
        ]

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        """Named series trimmed to `window`. A series with no point in the window is omitted."""
        if service != self._service:
            return []
        trimmed = [
            series.model_copy(
                update={"points": [p.model_copy() for p in series.points if window.contains(p.at)]}
            )
            for series in self._signals.metrics
            if series.name in names
        ]
        return [series for series in trimmed if series.points]

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        if service != self._service:
            return []
        return [
            deployment.model_copy(deep=True)
            for deployment in self._signals.deployments
            if window.contains(deployment.deployed_at)
        ]

    def get_config(self, service: str) -> dict[str, ConfigValue]:
        if service != self._service:
            return {}
        return dict(self._signals.config)

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        if service != self._service:
            return []
        return [status.model_copy(deep=True) for status in self._signals.dependencies]

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        """Change the served world to the scenario's post-action world for `action.action`.

        A key already in the ledger applies nothing and reports `applied=False`. An action the
        scenario has no post-action world for is refused with `ok=False`. Never raises.
        """
        if any(entry.idempotency_key == idempotency_key for entry in self._ledger):
            return ActionResult(
                ok=True,
                applied=False,
                idempotency_key=idempotency_key,
                detail=f"Key {idempotency_key} was already applied; nothing changed.",
            )
        world = self._worlds.get(action.action)
        if world is None:
            return ActionResult(
                ok=False,
                applied=False,
                idempotency_key=idempotency_key,
                detail=f"The simulator cannot model the action {action.action}; nothing changed.",
            )
        self._merge_world(world)
        self._ledger.append(
            AppliedAction(
                idempotency_key=idempotency_key,
                action=action.action,
                target=action.target,
                params=dict(action.params),
            )
        )
        return ActionResult(
            ok=True,
            applied=True,
            idempotency_key=idempotency_key,
            detail=f"Applied {action.action} to {action.target}.",
        )

    def ledger(self) -> list[AppliedAction]:
        return [entry.model_copy(deep=True) for entry in self._ledger]

    def _merge_world(self, world: PostActionWorld) -> None:
        series_by_id = {series.id: series for series in self._signals.metrics}
        for update in world.metrics:
            series_by_id[update.id].points.extend(p.model_copy() for p in update.points)
        self._signals.logs.extend(line.model_copy(deep=True) for line in world.logs)
