"""Shared fixtures: a scenario, its simulator source and its scripted model, with tracing off."""

from pathlib import Path

import pytest

from agentops.fakes import ScriptedChatModel, scripted_model_for
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.sources.simulator import SimulatorSource

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SCENARIO_IDS = list_scenario_ids(SCENARIOS_DIR)
TRACING_VARIABLES = [
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_ENDPOINT",
]


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer shell with LangSmith tracing on must not send test prompts anywhere."""
    for name in TRACING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(params=SCENARIO_IDS)
def scenario(request: pytest.FixtureRequest) -> Scenario:
    """Every scenario in turn; narrow it with `parametrize("scenario", [...], indirect=True)`."""
    return load_scenario(SCENARIOS_DIR / f"{request.param}.json")


@pytest.fixture
def source(scenario: Scenario) -> SimulatorSource:
    return SimulatorSource(scenario)


@pytest.fixture
def scripted_model(scenario: Scenario) -> ScriptedChatModel:
    return scripted_model_for(scenario, attempts=3)
