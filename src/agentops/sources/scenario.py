"""The scenario file format: signals a source may serve, and the sections it must never serve.

`signals` is what an investigator could observe. `ground_truth`, `required_evidence`,
`wrong_actions`, `expected_outcome`, `post_action_world` and `scripted_outputs` are the grading
key and test data; only `SimulatorSource.apply` reads `post_action_world`, and nothing serves it.
"""

from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from agentops.sources.base import (
    SIGNAL_ID_PATTERN,
    ConfigValue,
    DependencyStatus,
    Deployment,
    LogLine,
    Series,
)

ActionName = str


class ScenarioPart(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Signals(ScenarioPart):
    logs: list[LogLine]
    metrics: list[Series]
    deployments: list[Deployment]
    dependencies: list[DependencyStatus]
    config: dict[str, ConfigValue]

    def ids(self) -> list[str]:
        kinds: list[list[Any]] = [self.logs, self.metrics, self.deployments, self.dependencies]
        return [item.id for kind in kinds for item in kind]


class GroundTruth(ScenarioPart):
    root_cause: str = Field(pattern=SIGNAL_ID_PATTERN)
    correct_action: ActionName = Field(pattern=SIGNAL_ID_PATTERN)


class PostActionWorld(ScenarioPart):
    """Signals added once an action is applied: metric points extend the series with the same
    id, log lines are appended."""

    metrics: list[Series] = Field(default_factory=list)
    logs: list[LogLine] = Field(default_factory=list)


class ScriptedOutputs(ScenarioPart):
    """What the scripted chat model answers for this scenario. Kept as raw JSON objects here;
    the node that asks for a `Triage` or an `RcaOutput` validates them against that schema."""

    triage: dict[str, Any]
    rca: dict[str, Any]


class Scenario(ScenarioPart):
    id: str = Field(pattern=SIGNAL_ID_PATTERN)
    service: str
    symptoms: list[str] = Field(min_length=1)
    incident_time: AwareDatetime
    signals: Signals
    ground_truth: GroundTruth
    required_evidence: list[str]
    wrong_actions: list[ActionName]
    expected_outcome: Literal["remediate", "escalate", "loop"]
    post_action_world: dict[ActionName, PostActionWorld]
    scripted_outputs: ScriptedOutputs

    @model_validator(mode="after")
    def check_references(self) -> Self:
        problems = [
            *duplicate_signal_id_problems(self.signals),
            *unknown_required_evidence_problems(self),
            *post_action_world_problems(self),
        ]
        if self.ground_truth.correct_action in self.wrong_actions:
            problems.append(
                f"correct_action {self.ground_truth.correct_action!r} is also in wrong_actions"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_scenario(path: Path) -> Scenario:
    """Read and validate one scenario file. Raises `pydantic.ValidationError` naming the field."""
    return Scenario.model_validate_json(path.read_text(encoding="utf-8"))


def list_scenario_ids(directory: Path) -> list[str]:
    """Scenario ids are the file stems of the `*.json` files in `directory`, sorted."""
    return sorted(path.stem for path in directory.glob("*.json"))


def duplicate_signal_id_problems(signals: Signals) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for signal_id in signals.ids():
        if signal_id in seen and signal_id not in duplicates:
            duplicates.append(signal_id)
        seen.add(signal_id)
    return [f"signal id {signal_id!r} is declared more than once" for signal_id in duplicates]


def unknown_required_evidence_problems(scenario: Scenario) -> list[str]:
    declared = set(scenario.signals.ids())
    return [
        f"required_evidence id {evidence_id!r} is not a signal id"
        for evidence_id in scenario.required_evidence
        if evidence_id not in declared
    ]


def post_action_world_problems(scenario: Scenario) -> list[str]:
    metric_ids = {series.id for series in scenario.signals.metrics}
    declared = set(scenario.signals.ids())
    problems: list[str] = []
    for action, world in scenario.post_action_world.items():
        problems += [
            f"post_action_world[{action!r}] metric {series.id!r} extends no declared metric"
            for series in world.metrics
            if series.id not in metric_ids
        ]
        problems += [
            f"post_action_world[{action!r}] log {line.id!r} reuses a signal id"
            for line in world.logs
            if line.id in declared
        ]
    return problems
