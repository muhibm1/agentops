"""The registry: the only answer to "may this action run, with these parameters?".

Also the two pure helpers the approval node and the executor share: the idempotency key of a
remediation and the one-sentence dry-run description an operator approves.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path

from agentops.actions.runbook import ActionEntry, ParameterSpec, load_runbook
from agentops.state import Remediation

# src/agentops/actions/registry.py -> the repository root holds runbooks/.
DEFAULT_RUNBOOK_PATH = Path(__file__).resolve().parents[3] / "runbooks" / "default.yaml"
STRING_PARAMETER_MAX_LENGTH = 64
# A parameter name is echoed in an error only when it is this shape; any other name is model
# free text and is described, never repeated.
SAFE_PARAMETER_NAME = re.compile(r"[a-z0-9_]{1,64}")
UNSAFE_NAME_PLACEHOLDER = "<unsafe parameter name>"


class UnknownActionError(KeyError):
    """The action is not declared in the runbook."""

    def __str__(self) -> str:
        return str(self.args[0])


class ParameterError(ValueError):
    """One or more parameters break the declared schema. `parameters` names each, sorted."""

    def __init__(self, action: str, problems: dict[str, str]) -> None:
        self.parameters = sorted(problems)
        sentences = " ".join(problems[name] for name in self.parameters)
        super().__init__(f"Parameters for {action} were rejected. {sentences}")


class Registry:
    def __init__(self, entries: list[ActionEntry]) -> None:
        self._entries = {entry.action: entry for entry in entries}

    def names(self) -> list[str]:
        return sorted(self._entries)

    def has(self, name: str) -> bool:
        return name in self._entries

    def get(self, name: str) -> ActionEntry:
        entry = self._entries.get(name)
        if entry is None:
            raise UnknownActionError(f"The action {safe_name(name)} is not in the runbook.")
        return entry

    def validate_params(
        self, name: str, params: Mapping[str, object], target: str | None = None
    ) -> None:
        """Raise ParameterError naming every undeclared, missing or out-of-schema parameter, and
        `target` when it is given and does not match the action's `target_pattern`."""
        entry = self.get(name)
        declared = entry.params
        problems: dict[str, str] = {}
        if target is not None and re.fullmatch(entry.target_pattern, target) is None:
            problems["target"] = (
                f"The target does not match the pattern {entry.target_pattern} "
                f"that {name} declares."
            )
        for key in params:
            if key not in declared:
                shown = safe_name(key)
                problems[shown] = f"Parameter {shown} is not declared."
        for key, spec in declared.items():
            problem = parameter_problem(key, spec, params)
            if problem is not None:
                problems[key] = problem
        if problems:
            raise ParameterError(name, problems)


def default_registry() -> Registry:
    """The registry over `runbooks/default.yaml`. Raises RunbookError if the file is bad."""
    return Registry(load_runbook(DEFAULT_RUNBOOK_PATH))


def describe_actions(registry: Registry) -> str:
    """One prompt line naming every action the runbook declares, with its target pattern and
    parameters, so a model proposes a name the registry will accept. The runbook is the same for
    every incident, so the line tells a model nothing about which action is right."""
    actions = []
    for name in registry.names():
        entry = registry.get(name)
        params = ", ".join(
            f"{key} ({spec.type}{', required' if spec.required else ''})"
            for key, spec in entry.params.items()
        )
        actions.append(
            f"{name} (target matching {entry.target_pattern}; params: {params or 'none'})"
        )
    return "A remediation must name one of these runbook actions, spelled exactly: " + "; ".join(
        actions
    )


def idempotency_key(incident_id: str, remediation: Remediation) -> str:
    """sha256 of the incident id followed by the sorted-key JSON of action, target and params.

    Parameter order never changes the key; a different incident always does.
    """
    canonical = json.dumps(
        {
            "action": remediation.action,
            "target": remediation.target,
            "params": remediation.params,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((incident_id + canonical).encode("utf-8")).hexdigest()


def dry_run_description(entry: ActionEntry, remediation: Remediation) -> str:
    """One sentence saying what applying `remediation` would do, at the runbook's risk."""
    if remediation.params:
        arguments = ", ".join(
            f"{key}={json.dumps(value)}" for key, value in sorted(remediation.params.items())
        )
    else:
        arguments = "no parameters"
    return (
        f"Would apply {entry.action} to {entry.target_kind} {remediation.target} with "
        f"{arguments} (risk {entry.risk}); nothing changes until an operator approves."
    )


def safe_name(name: str) -> str:
    return name if SAFE_PARAMETER_NAME.fullmatch(name) else UNSAFE_NAME_PLACEHOLDER


def parameter_problem(key: str, spec: ParameterSpec, params: Mapping[str, object]) -> str | None:
    """The sentence describing what is wrong with `params[key]`, or None. Never echoes a value."""
    if key not in params:
        return f"Parameter {key} is required." if spec.required else None
    value = params[key]
    if spec.type == "boolean":
        return None if isinstance(value, bool) else f"Parameter {key} must be a boolean."
    if spec.type == "string":
        return string_problem(key, spec, value)
    return number_problem(key, spec, value)


def string_problem(key: str, spec: ParameterSpec, value: object) -> str | None:
    if not isinstance(value, str):
        return f"Parameter {key} must be a string."
    if len(value) > STRING_PARAMETER_MAX_LENGTH:
        return f"Parameter {key} is longer than {STRING_PARAMETER_MAX_LENGTH} characters."
    if spec.pattern is not None and re.fullmatch(spec.pattern, value) is None:
        return f"Parameter {key} does not match the pattern {spec.pattern}."
    return None


def number_problem(key: str, spec: ParameterSpec, value: object) -> str | None:
    wanted = "an integer" if spec.type == "integer" else "a finite number"
    # bool is a subclass of int in Python, so True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return f"Parameter {key} must be {wanted}."
    if spec.type == "integer" and not isinstance(value, int):
        return f"Parameter {key} must be {wanted}."
    if not math.isfinite(value):
        return f"Parameter {key} must be {wanted}."
    if spec.min is not None and value < spec.min:
        return f"Parameter {key} is below the minimum {spec.min:g}."
    if spec.max is not None and value > spec.max:
        return f"Parameter {key} is above the maximum {spec.max:g}."
    return None
