"""E41 and F9: `langgraph.json` points `langgraph dev` at a factory that builds the graph for one
scenario with the scripted model (R16, R17).

No test starts the dev server or runs the `langgraph` command line.
"""

import importlib
import json
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agentops import dev_server
from agentops.sources.scenario import Scenario, load_scenario
from agentops.state import new_incident

REPOSITORY = Path(__file__).resolve().parents[1]
LANGGRAPH_JSON = REPOSITORY / "langgraph.json"


def graph_entries() -> dict[str, str]:
    return json.loads(LANGGRAPH_JSON.read_text(encoding="utf-8"))["graphs"]


# E41: the file parses, names only this package, carries no environment, and every graph path
# resolves to a factory that compiles the graph, for a named scenario and for the default.
def test_langgraph_json_resolves_to_a_factory_that_compiles_the_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    document = json.loads(LANGGRAPH_JSON.read_text(encoding="utf-8"))

    assert document["dependencies"] == ["."]
    assert "env" not in document
    assert list(document["graphs"]) == ["agentops"]
    for entry in graph_entries().values():
        file_part, attribute = entry.split(":")
        path = (REPOSITORY / file_part).resolve()
        assert path.is_file()
        module_name = ".".join(path.relative_to(REPOSITORY / "src").with_suffix("").parts)
        factory = getattr(importlib.import_module(module_name), attribute)

        named = factory({"configurable": {"scenario_id": "clean_bad_deploy"}})
        default = factory({})

        assert isinstance(named, CompiledStateGraph)
        assert isinstance(default, CompiledStateGraph)
    assert list(tmp_path.iterdir()) == []


# F9: a malformed or unknown scenario id is refused by name before any scenario file is read.
@pytest.mark.parametrize("scenario_id", ["../etc", "..\\etc", "no_such_scenario", "", 7])
def test_the_factory_refuses_an_unknown_or_malformed_scenario_id(
    scenario_id: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []

    def recording_load(path: Path) -> Scenario:
        opened.append(path)
        raise AssertionError("no scenario file may be opened for a refused id")

    monkeypatch.setattr(dev_server, "load_scenario", recording_load)

    with pytest.raises(ValueError, match="scenario"):
        dev_server.make_graph({"configurable": {"scenario_id": scenario_id}})

    assert opened == []


# The dev server's graph is compiled without a checkpointer: run without one, it stops at the
# approval pause, an approval cannot resume it, and nothing is applied.
def test_the_factory_graph_never_applies_without_a_checkpointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario = load_scenario(REPOSITORY / "scenarios" / "clean_bad_deploy.json")
    incident = new_incident(
        scenario.service,
        scenario.symptoms,
        opened_at=scenario.incident_time,
        scenario_id=scenario.id,
    )
    graph = dev_server.make_graph({})
    config: RunnableConfig = {"configurable": {"thread_id": incident["incident_id"]}}

    paused = graph.invoke(incident, config)
    with pytest.raises(RuntimeError, match="checkpointer"):
        graph.invoke(Command(resume={"decision": "approve", "operator": "operator"}), config)

    assert paused["status"] == "awaiting_approval"
    assert paused["execution"] is None
    audit_lines = (tmp_path / "audit" / f"{incident['incident_id']}.jsonl").read_text("utf-8")
    events = [json.loads(line)["event"] for line in audit_lines.splitlines()]
    assert events == ["proposal"]


# Security M1: the dev server's default CORS answer is `*`; the file narrows it to local origins.
def test_langgraph_json_pins_the_dev_server_origins() -> None:
    cors = json.loads(LANGGRAPH_JSON.read_text(encoding="utf-8"))["http"]["cors"]

    assert "*" not in cors["allow_origins"]
    assert cors["allow_credentials"] is False
