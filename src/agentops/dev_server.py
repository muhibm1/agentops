"""The graph factory `langgraph.json` names for `langgraph dev`: one scenario, the scripted model.

`make_graph` compiles the graph without a checkpointer; the dev server supplies its own (believed
from the LangGraph docs, not verified here). Without one a run still stops at the approval pause,
and an approval cannot resume it (LangGraph raises), so nothing is applied
(`tests/test_langgraph_json.py`).
"""

import re
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import scripted_model_for
from agentops.graph import build_state_graph
from agentops.sources.base import SIGNAL_ID_PATTERN
from agentops.sources.scenario import list_scenario_ids, load_scenario
from agentops.sources.simulator import SimulatorSource

DEFAULT_SCENARIO_ID = "clean_bad_deploy"
# Resolved from this file, so the server finds the scenarios whatever its working directory.
SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"
# Relative to the server's working directory, like the command line's default; git-ignored.
AUDIT_DIR = Path("audit")


def make_graph(config: RunnableConfig) -> CompiledStateGraph:
    """The graph for `configurable.scenario_id` (default `clean_bad_deploy`).

    Raises `ValueError` naming the id when it is not a scenario file stem in `scenarios/`; the id
    is checked against the signal id pattern first, so it can never name another path.
    """
    scenario_id = scenario_id_in(config)
    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")
    graph = build_state_graph(
        SimulatorSource(scenario),
        scripted_model_for(scenario),
        default_registry(),
        AuditLog(AUDIT_DIR),
    )
    return graph.compile()


def scenario_id_in(config: RunnableConfig) -> str:
    configurable: dict[str, Any] = dict(config.get("configurable") or {})
    scenario_id = configurable.get("scenario_id", DEFAULT_SCENARIO_ID)
    is_safe = isinstance(scenario_id, str) and re.fullmatch(SIGNAL_ID_PATTERN, scenario_id)
    if not is_safe or scenario_id not in list_scenario_ids(SCENARIOS_DIR):
        raise ValueError(
            f"Unknown scenario {scenario_id!r}; choose one of "
            f"{', '.join(list_scenario_ids(SCENARIOS_DIR))}."
        )
    return scenario_id
