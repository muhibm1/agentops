"""Long-term memory: a graph compiled with a LangGraph store remembers every finished incident
across threads, and a graph without one behaves exactly as before."""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from typer.testing import CliRunner

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.cli import app
from agentops.fakes import scripted_model_for
from agentops.graph import build_graph, checkpoint_serializer
from agentops.memory import NAMESPACE, past_incidents, remember_incident
from agentops.sources.scenario import load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import ApprovalDecision, new_incident

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "scenarios"
runner = CliRunner()


@contextmanager
def graph_for(scenario_id: str, store: InMemoryStore | None) -> Iterator[CompiledStateGraph]:
    """The full graph over one scenario, on an in-memory checkpointer, with or without a store."""
    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")
    with tempfile.TemporaryDirectory() as audit_dir:
        yield build_graph(
            SimulatorSource(scenario),
            scripted_model_for(scenario),
            default_registry(),
            AuditLog(Path(audit_dir)),
            InMemorySaver(serde=checkpoint_serializer()),
            store=store,
        )


def run_to_end(scenario_id: str, store: InMemoryStore | None, thread: str = "t") -> dict[str, Any]:
    """Run a scenario, approving every pause, and return its final state."""
    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")
    incident = new_incident(
        scenario.service, scenario.symptoms, opened_at=scenario.incident_time, incident_id=thread
    )
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 60}
    with graph_for(scenario_id, store) as graph:
        graph.invoke(incident, config)  # type: ignore[arg-type]
        for _ in range(5):
            if not graph.get_state(config).interrupts:  # type: ignore[arg-type]
                break
            graph.invoke(Command(resume=ApprovalDecision(decision="approve")), config)  # type: ignore[arg-type]
        return dict(graph.get_state(config).values)  # type: ignore[arg-type]


def test_a_resolved_incident_is_remembered_under_its_service() -> None:
    store = InMemoryStore()
    state = run_to_end("clean_bad_deploy", store)
    assert state["status"] == "resolved"
    [record] = past_incidents(store)
    assert record["thread_id"] == "t"
    assert record["service"] == state["service"]
    assert record["status"] == "resolved"
    assert record["root_cause"] == "recent_deploy_regression"
    assert record["action"] == "rollback_deploy"
    assert record["applied"] is True
    assert record["summary"]
    assert store.get((NAMESPACE, state["service"]), "t") is not None


def test_an_escalation_is_remembered_with_its_reason() -> None:
    store = InMemoryStore()
    state = run_to_end("ambiguous_two_causes", store)
    [record] = past_incidents(store, state["service"])
    assert record["status"] == "escalated"
    assert record["escalation_reason"] == state["escalation_reason"]
    assert record["action"] is None


def test_incidents_of_two_threads_are_kept_apart_and_filtered_by_service() -> None:
    store = InMemoryStore()
    first = run_to_end("clean_bad_deploy", store, thread="a")
    second = run_to_end("ambiguous_two_causes", store, thread="b")
    assert first["service"] != second["service"]
    assert {record["thread_id"] for record in past_incidents(store)} == {"a", "b"}
    assert [record["thread_id"] for record in past_incidents(store, first["service"])] == ["a"]
    assert len(past_incidents(store, limit=1)) == 1


def test_the_store_changes_nothing_the_graph_decides() -> None:
    with_store = run_to_end("deploy_red_herring_db", InMemoryStore())
    without = run_to_end("deploy_red_herring_db", None)
    for key in ("status", "hypotheses", "root_cause", "remediation", "loops"):
        assert with_store[key] == without[key]
    assert len(with_store["evidence"]) == len(without["evidence"])


def test_remembering_outside_a_graph_run_does_nothing() -> None:
    remember_incident({"incident_id": "x", "service": "checkout"})


def cli(tmp_path: Path, *args: str) -> str:
    """Run one command over files under `tmp_path`; it must exit 0."""
    files = ["--state-db", str(tmp_path / "s.sqlite")]
    if args[0] != "incidents":
        files += ["--audit-dir", str(tmp_path / "audit"), "--scenarios-dir", str(SCENARIOS_DIR)]
    result = runner.invoke(app, [*args, *files])
    assert result.exit_code == 0, result.output
    return result.output


def test_the_incidents_command_lists_what_the_thread_commands_remembered(tmp_path: Path) -> None:
    assert cli(tmp_path, "incidents") == "No incidents recorded yet.\n"
    cli(tmp_path, "run", "--scenario", "clean_bad_deploy", "--model", "scripted", "--thread", "r")
    assert "No incidents recorded yet." in cli(tmp_path, "incidents")  # paused, not finished
    cli(tmp_path, "approve", "r", "--decision", "approve")
    cli(
        tmp_path,
        "run",
        "--scenario",
        "ambiguous_two_causes",
        "--model",
        "scripted",
        "--thread",
        "e",
    )
    lines = cli(tmp_path, "incidents").splitlines()
    assert len(lines) == 2
    resolved = next(line for line in lines if " r  " in line)
    assert "status: resolved" in resolved
    assert "action: rollback_deploy applied" in resolved
    escalated = next(line for line in lines if " e  " in line)
    assert "status: escalated" in escalated and "reason: " in escalated
    service = resolved.split()[1]
    assert cli(tmp_path, "incidents", "--service", service).splitlines() == [resolved]
