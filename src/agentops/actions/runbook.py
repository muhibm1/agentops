"""The typed runbook: what each allowlisted action is, what it accepts and what must hold first.

`load_runbook` parses YAML with `yaml.safe_load` only and validates every entry, so a bad file
fails at startup with a sentence naming the file, the entry index and the field.
"""

import re
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ACTION_NAME = r"^[a-z0-9_]+$"


class RunbookError(ValueError):
    """The runbook file is unreadable or an entry does not match the schema."""


class RunbookModel(BaseModel):
    """Base for every runbook part: an unknown key is a typo or an attack, never data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ParameterSpec(RunbookModel):
    type: Literal["integer", "number", "string", "boolean"]
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    required: bool = False

    @model_validator(mode="after")
    def check_constraints_fit_the_type(self) -> Self:
        is_numeric = self.type in ("integer", "number")
        if (self.min is not None or self.max is not None) and not is_numeric:
            raise ValueError("min and max apply only to integer and number parameters.")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min {self.min} is above max {self.max}.")
        if self.pattern is not None and self.type != "string":
            raise ValueError("pattern applies only to string parameters.")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ValueError(f"pattern does not compile ({error}).") from error
        return self


class MetricAbove(RunbookModel):
    check: Literal["metric_above"]
    metric: str = Field(pattern=ACTION_NAME)
    value: float


class MetricBelow(RunbookModel):
    check: Literal["metric_below"]
    metric: str = Field(pattern=ACTION_NAME)
    value: float


class DeploymentWithinMinutes(RunbookModel):
    check: Literal["deployment_within_minutes"]
    minutes: int = Field(gt=0)


class PreviousVersionAvailable(RunbookModel):
    check: Literal["previous_version_available"]


class DependencyNotHealthy(RunbookModel):
    """Checks the dependency the remediation targets."""

    check: Literal["dependency_not_healthy"]


class ConfigTrue(RunbookModel):
    check: Literal["config_true"]
    key: str = Field(pattern=ACTION_NAME)


Precondition = Annotated[
    MetricAbove
    | MetricBelow
    | DeploymentWithinMinutes
    | PreviousVersionAvailable
    | DependencyNotHealthy
    | ConfigTrue,
    Field(discriminator="check"),
]


class RollbackDeclaration(RunbookModel):
    """How to undo the action: a sentence for the operator, and the undoing action if one exists."""

    description: str = Field(min_length=1, max_length=200)
    action: str | None = Field(default=None, pattern=ACTION_NAME)
    params_from: Literal["previous"] | None = None

    @model_validator(mode="after")
    def check_action_and_params_come_together(self) -> Self:
        if (self.action is None) != (self.params_from is None):
            raise ValueError("rollback action and params_from must both be present or absent.")
        return self


class ActionEntry(RunbookModel):
    action: str = Field(pattern=ACTION_NAME)
    target_kind: Literal["database", "deployment", "dependency", "job"]
    params: dict[str, ParameterSpec]
    risk: Literal["low", "medium", "high"]
    preconditions: list[Precondition] = Field(min_length=1)
    dry_run: Literal["describe"]
    rollback: RollbackDeclaration
    idempotent: bool


def load_runbook(path: Path) -> list[ActionEntry]:
    """Parse and validate the runbook at `path`. Raises RunbookError naming the problem."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RunbookError(f"{path}: cannot read the runbook as YAML ({error}).") from error
    if not isinstance(document, list):
        raise RunbookError(f"{path}: root must be a list of action entries.")
    entries = [validated_entry(path, index, raw) for index, raw in enumerate(document)]
    reject_duplicate_names(path, entries)
    return entries


def validated_entry(path: Path, index: int, raw: object) -> ActionEntry:
    try:
        return ActionEntry.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "entry"
        raise RunbookError(f"{path}: entry {index} field {field}: {first['msg']}.") from error


def reject_duplicate_names(path: Path, entries: list[ActionEntry]) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if entry.action in seen:
            raise RunbookError(
                f"{path}: entry {index} field action: {entry.action} is declared twice."
            )
        seen.add(entry.action)
