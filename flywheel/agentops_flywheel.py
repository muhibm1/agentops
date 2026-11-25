"""Runs the AgentOps graph on one Flywheel test case, graded by the `agentops eval` evaluators.

The model comes from `FLYWHEEL_AGENTOPS_MODEL` (`scripted` by default, or `anthropic`, which is
paid under `AGENTOPS_API_CAP`); `FLYWHEEL_AGENTOPS_DEV=1` picks the cheaper models. Held-out
scenarios are refused so the frozen split never steers changes.
"""

import os
from pathlib import Path
from typing import Any

from langsmith import traceable

from agentops.evals.dataset import held_out_ids_of
from agentops.evals.outcome import reference_for
from agentops.evals.run import measured_run
from agentops.models import model_factory
from agentops.sources.scenario import Scenario, load_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO_ROOT / "scenarios"
EVALS_DIR = REPO_ROOT / "evals"


class HeldOutCaseError(ValueError):
    """A test case names a held-out scenario."""


def scenario_for(inputs: dict[str, Any]) -> Scenario:
    """The scenario a case runs in, with the case's own wording of the symptoms."""
    scenario_id = str(inputs["scenario_id"])
    if scenario_id in held_out_ids_of(EVALS_DIR):
        raise HeldOutCaseError(f"{scenario_id} is in the frozen held-out split")
    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")
    symptoms = inputs.get("symptoms")
    if symptoms and list(symptoms) != list(scenario.symptoms):
        scenario = scenario.model_copy(update={"symptoms": list(symptoms)})
    return scenario


def target(inputs: dict[str, Any]) -> dict[str, Any]:
    """One graph run to its end, approving every pause, as the outcome the evaluators grade."""
    model = os.environ.get("FLYWHEEL_AGENTOPS_MODEL", "scripted")
    dev = os.environ.get("FLYWHEEL_AGENTOPS_DEV") == "1"
    models = model_factory(model, dev)  # type: ignore[arg-type]
    outcome = measured_run("graph", scenario_for(inputs), {}, models=models)
    return outcome.model_dump(mode="json")


# The same run as one named trace, for traffic sent to a tracing project (traffic.py).
incident = traceable(name="agentops-incident", run_type="chain")(target)


def reference(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """The grading key for a case: the scenario's ground truth. None for a held-out scenario."""
    try:
        return reference_for(scenario_for(inputs))
    except HeldOutCaseError:
        return None


def case_inputs(trace_inputs: dict[str, Any]) -> dict[str, Any] | None:
    """A trace's inputs as a test case's inputs; None when the trace is not an incident run."""
    inputs = trace_inputs.get("inputs", trace_inputs)
    if not isinstance(inputs, dict) or "scenario_id" not in inputs:
        return None
    return {
        "scenario_id": inputs["scenario_id"],
        "service": inputs.get("service"),
        "symptoms": list(inputs.get("symptoms") or []),
    }
