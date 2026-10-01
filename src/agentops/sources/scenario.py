"""The scenario file format: signals a source may serve, and the sections it must never serve.

`signals` is what an investigator could observe. `ground_truth`, `required_evidence`,
`wrong_actions`, `expected_outcome`, `post_action_world` and `scripted_outputs` are the grading
key and test data; only `SimulatorSource.apply` reads `post_action_world`, and nothing serves it.
"""

import re
from collections.abc import Iterable
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
COMBINED_WORLD_SEPARATOR = "+"


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
    id, log lines are appended.

    A world is keyed by one action name, or by several distinct names sorted and joined by `+`
    (see `combined_world_key`): that combined world is served once all of them are applied."""

    metrics: list[Series] = Field(default_factory=list)
    logs: list[LogLine] = Field(default_factory=list)


class ScriptedOutputs(ScenarioPart):
    """What the scripted chat model answers for this scenario. Kept as raw JSON objects here;
    the node that asks for a `Triage`, an `RcaOutput`, an `Escalation` or a `Postmortem`
    validates them against that schema.

    `escalate` is present only for scenarios whose run ends in escalation; `postmortem` for
    scenarios whose run applies an action; `rca_after_action` is the rca answer once an action
    is applied, required for a `loop` scenario."""

    triage: dict[str, Any]
    rca: dict[str, Any]
    escalate: dict[str, Any] | None = None
    rca_after_action: dict[str, Any] | None = None
    postmortem: dict[str, Any] | None = None


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
            *missing_scripted_output_problems(self),
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
    return Scenario.model_validate_json(path.read_text(encoding="utf-8-sig"))


def list_scenario_ids(directory: Path) -> list[str]:
    """Scenario ids are the file stems of the `*.json` files in `directory`, sorted."""
    return sorted(path.stem for path in directory.glob("*.json"))


def combined_world_key(action_names: Iterable[str]) -> str:
    """The `post_action_world` key for a set of applied actions: distinct names, sorted, `+`."""
    return COMBINED_WORLD_SEPARATOR.join(sorted(set(action_names)))


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
        problems += world_key_problems(action)
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


def world_key_problems(key: str) -> list[str]:
    names = key.split(COMBINED_WORLD_SEPARATOR)
    names_are_ids = all(re.fullmatch(SIGNAL_ID_PATTERN, name) for name in names)
    if names_are_ids and key == combined_world_key(names) and len(set(names)) == len(names):
        return []
    return [
        f"post_action_world key {key!r} must be one action name, or distinct action names "
        f"sorted and joined by {COMBINED_WORLD_SEPARATOR!r}"
    ]


def missing_scripted_output_problems(scenario: Scenario) -> list[str]:
    outputs = scenario.scripted_outputs
    problems: list[str] = []
    if scenario.expected_outcome in ("remediate", "loop") and outputs.postmortem is None:
        problems.append(
            f"scripted_outputs.postmortem is required for a {scenario.expected_outcome} scenario"
        )
    if scenario.expected_outcome == "loop" and outputs.rca_after_action is None:
        problems.append("scripted_outputs.rca_after_action is required for a loop scenario")
    return problems
