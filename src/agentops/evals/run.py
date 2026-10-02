"""Run the graph and the ReAct baseline over a split, grade every run, and render the results.

Every run is self-contained so a measurement writes nothing under the working directory: a
simulator with an in-memory ledger, the audit log and the SQLite checkpoints in a temporary
directory removed after the run, and an automatic approval by the role `operator`. The
checkpointer is the same `SqliteSaver` the command line uses, because no module under `src/`
may use LangGraph's in-memory saver (`tests/test_full_runs.py`). A run that raises is a
`failed` row naming the exception type, never a crash: the measurement completed and the
failure is part of the result.
"""

import json
import sqlite3
import statistics
import tempfile
import time
import traceback
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.baseline import BaselineRun, run_baseline
from agentops.evals.dataset import Split
from agentops.evals.evaluators import DETERMINISTIC_EVALUATORS, EvaluatorResult
from agentops.evals.outcome import (
    UNDETERMINED_LABEL,
    RunOutcome,
    SystemName,
    evidence_ids_of,
    inputs_for,
    outcome_from_state,
    reference_for,
)
from agentops.fakes import scripted_baseline_model_for, scripted_model_for
from agentops.graph import build_graph, checkpoint_serializer
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import IncidentState, new_incident
from agentops.tracing import PROJECT_VARIABLE, TracingStatus

SYSTEMS: tuple[SystemName, ...] = get_args(SystemName)
RECURSION_LIMIT = 60
CHECKPOINT_FILE = "checkpoints.sqlite"
AUTO_APPROVAL = {"decision": "approve", "operator": "operator"}
# A scenario that loops needs one approval per action; a run still paused after this many is a
# failed row (`evidence_ids_at_proposal` raises).
FAILURE_MESSAGE_MAX_LENGTH = 200
MAX_APPROVALS = 5
PASS_FAIL_EVALUATORS = (
    "root_cause_accuracy",
    "correct_outcome",
    "evidence_sufficiency",
    "false_remediation",
    "safety",
)
SCRIPTED_SENTENCE = "Scripted model: these numbers measure the harness, not a model."
FROZEN_WARNING = (
    "Frozen split: score it only when a version is frozen, and never change the system to "
    "improve these numbers."
)


@dataclass(frozen=True)
class EvalRow:
    """One run of one system over one scenario, with every evaluator's result."""

    system: SystemName
    scenario_id: str
    repetition: int
    outcome: RunOutcome
    evaluators: dict[str, EvaluatorResult]


def run_split(
    scenarios: Sequence[Scenario],
    *,
    systems: Sequence[SystemName],
    repetitions: int,
    split: Split,
    tracing: TracingStatus,
    report: Callable[[str], None] = lambda text: None,
) -> list[EvalRow]:
    """Each system over each scenario, `repetitions` times, graded by the seven evaluators.

    `report` receives one summary line for every run that raised."""
    rows: list[EvalRow] = []
    for system in systems:
        for scenario in scenarios:
            for repetition in range(1, repetitions + 1):
                config = trace_config(system, scenario.id, repetition, split, tracing)
                outcome = measured_run(system, scenario, config, report)
                rows.append(
                    EvalRow(
                        system=system,
                        scenario_id=scenario.id,
                        repetition=repetition,
                        outcome=outcome,
                        evaluators=graded(scenario, outcome),
                    )
                )
    return rows


def run_graph_once(scenario: Scenario, *, config: RunnableConfig) -> RunOutcome:
    """One graph run to its end, approving every pause, reduced to a `RunOutcome`."""
    registry = default_registry()
    incident = new_incident(
        scenario.service,
        scenario.symptoms,
        opened_at=scenario.incident_time,
        scenario_id=scenario.id,
    )
    run_config: RunnableConfig = {
        **config,
        "configurable": {"thread_id": incident["incident_id"]},
        "recursion_limit": RECURSION_LIMIT,
    }
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="agentops-eval-") as run_dir:
        connection = sqlite3.connect(Path(run_dir) / CHECKPOINT_FILE, check_same_thread=False)
        # Closed before the directory is removed: Windows cannot delete an open file.
        with closing(connection):
            graph = build_graph(
                SimulatorSource(scenario),
                scripted_model_for(scenario),
                registry,
                AuditLog(Path(run_dir)),
                SqliteSaver(connection, serde=checkpoint_serializer()),
            )
            evidence_ids = evidence_ids_at_proposal(graph, incident, run_config)
            state = IncidentState(**graph.get_state(run_config).values)
    finished = time.perf_counter()
    return outcome_from_state(state, evidence_ids, started, finished, registry=registry)


def run_baseline_once(scenario: Scenario, *, config: RunnableConfig) -> RunOutcome:
    """One baseline run over a fresh simulator, reduced to a `RunOutcome`."""
    started = time.perf_counter()
    # Wrapped as one runnable so that, when tracing is on, the trace carries the run name and
    # tags of `config` and the agent's own runs nest under it.
    baseline = RunnableLambda(
        lambda _: run_baseline(
            scenario, scripted_baseline_model_for(scenario), incident_id=f"eval-{scenario.id}"
        )
    )
    run: BaselineRun = baseline.invoke(None, config)
    return outcome_from_baseline(run, started, time.perf_counter())


RUNNERS: dict[SystemName, Callable[..., RunOutcome]] = {
    "graph": run_graph_once,
    "baseline": run_baseline_once,
}


def measured_run(
    system: SystemName,
    scenario: Scenario,
    config: RunnableConfig,
    report: Callable[[str], None] = lambda text: None,
) -> RunOutcome:
    started = time.perf_counter()
    try:
        return RUNNERS[system](scenario, config=config)
    except Exception as error:  # a run that raises is a measured failure, shown in the table
        report(failure_summary(system, scenario.id, error))
        return failed_outcome(system, scenario.id, error, started, time.perf_counter())


def failure_summary(system: SystemName, scenario_id: str, error: Exception) -> str:
    """One line for the operator: the run, the exception type and message, and where it was
    raised. The table keeps only the type name; this is what lets someone find the cause."""
    message = str(error)[:FAILURE_MESSAGE_MAX_LENGTH]
    frames = traceback.extract_tb(error.__traceback__)
    where = f" at {frames[-1].filename}:{frames[-1].lineno} in {frames[-1].name}" if frames else ""
    return f"run failed: {system} {scenario_id}: {type(error).__name__}: {message}{where}"


def trace_config(
    system: SystemName, scenario_id: str, repetition: int, split: Split, tracing: TracingStatus
) -> RunnableConfig:
    """The run name, tags and metadata of a traced run; nothing at all when tracing is off."""
    if not tracing.enabled:
        return {}
    return {
        "run_name": f"{system}/{scenario_id}/{repetition}",
        "tags": [f"system:{system}", f"split:{split}"],
        "metadata": {"scenario_id": scenario_id, "repetition": repetition},
    }


def project_environment(tracing: TracingStatus) -> dict[str, str]:
    """The environment that sends traces to the status's project; empty when tracing is off."""
    if not tracing.enabled or tracing.project is None:
        return {}
    return {PROJECT_VARIABLE: tracing.project}


def render_header(
    *, model: str, split: Split, scenario_count: int, repetitions: int, tracing: TracingStatus
) -> list[str]:
    scenarios = "scenario" if scenario_count == 1 else "scenarios"
    tracing_text = f"on, project {tracing.project}" if tracing.enabled else "off"
    lines = [
        f"model: {model}  split: {split}  {scenario_count} {scenarios}  "
        f"repetitions {repetitions}  tracing: {tracing_text}"
    ]
    if model == "scripted":
        lines.append(SCRIPTED_SENTENCE)
    if split == "held_out":
        lines.append(FROZEN_WARNING)
    return lines


def render_table(rows: Sequence[EvalRow]) -> list[str]:
    """One line per system (runs, pass counts, tool-call and latency spread), then every
    failing (system, scenario, repetition, evaluator) and every run that failed outright."""
    headings = [
        "system",
        "runs",
        *PASS_FAIL_EVALUATORS,
        "tool_calls mean/min/max",
        "latency_ms mean/min/max",
    ]
    measured = {row.system for row in rows}
    table = [headings, *(summary_cells(system, rows) for system in SYSTEMS if system in measured)]
    widths = [max(len(line[column]) for line in table) for column in range(len(headings))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True))
        for line in table
    ]
    return [*(line.rstrip() for line in lines), *failure_lines(rows)]


def rows_as_json(rows: Sequence[EvalRow]) -> str:
    """One object per run: system, scenario, repetition, the `RunOutcome` and every result."""
    return json.dumps(
        [
            {
                "system": row.system,
                "scenario_id": row.scenario_id,
                "repetition": row.repetition,
                "outcome": row.outcome.model_dump(mode="json"),
                "evaluators": row.evaluators,
            }
            for row in rows
        ],
        indent=2,
    )


def evidence_ids_at_proposal(
    graph: CompiledStateGraph, incident: IncidentState, config: RunnableConfig
) -> list[str]:
    """Runs the graph to its end, approving each pause, and returns the evidence ids held the
    first time the run proposed a remediation or named an escalation reason.

    Raises `RuntimeError` when the run is still paused after `MAX_APPROVALS` approvals, or ended
    without proposing or escalating: the ids of a run that never decided say nothing about
    evidence sufficiency, so the run must become a failed row, not a quietly wrong one."""
    captured: list[str] | None = None
    value: Any = incident
    for _ in range(MAX_APPROVALS + 1):
        for chunk in graph.stream(value, config, stream_mode="values"):
            if captured is None and has_decided(chunk):
                captured = evidence_ids_of(IncidentState(**chunk))
        if not graph.get_state(config).interrupts:
            break
        value = Command(resume=AUTO_APPROVAL)
    if graph.get_state(config).interrupts:
        raise RuntimeError(f"the run was still waiting for an approval after {MAX_APPROVALS}")
    if captured is None:
        raise RuntimeError("the run ended without proposing a remediation or escalating")
    return captured


def has_decided(chunk: Any) -> bool:
    if not isinstance(chunk, dict):
        return False
    return chunk.get("remediation") is not None or chunk.get("escalation_reason") is not None


def outcome_from_baseline(run: BaselineRun, started: float, finished: float) -> RunOutcome:
    proposed = [attempt.action for attempt in run.applies]
    if run.answer is not None and run.answer.action:
        proposed.append(run.answer.action)
    return RunOutcome(
        system="baseline",
        scenario_id=run.scenario_id,
        root_cause_label=run.root_cause_label,
        explanation=None,
        cited_evidence_ids=[],
        outcome=run.outcome,
        failure=run.failure,
        evidence_ids_before_proposal=run.evidence_ids_before_apply,
        proposed_actions=list(dict.fromkeys(proposed)),
        applies=run.applies,
        tool_calls=len(run.tool_calls),
        model_calls=run.model_calls,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        latency_ms=elapsed_ms(started, finished),
        loops=0,
    )


def failed_outcome(
    system: SystemName, scenario_id: str, error: Exception, started: float, finished: float
) -> RunOutcome:
    return RunOutcome(
        system=system,
        scenario_id=scenario_id,
        root_cause_label=UNDETERMINED_LABEL,
        explanation=None,
        cited_evidence_ids=[],
        outcome="failed",
        failure=type(error).__name__,
        evidence_ids_before_proposal=[],
        proposed_actions=[],
        applies=[],
        tool_calls=0,
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
        latency_ms=elapsed_ms(started, finished),
        loops=0,
    )


def graded(scenario: Scenario, outcome: RunOutcome) -> dict[str, EvaluatorResult]:
    inputs = inputs_for(scenario)
    outputs = outcome.model_dump(mode="json")
    reference = reference_for(scenario)
    return {
        evaluator.__name__: evaluator(inputs, outputs, reference)
        for evaluator in DETERMINISTIC_EVALUATORS
    }


def summary_cells(system: SystemName, rows: Sequence[EvalRow]) -> list[str]:
    runs = [row for row in rows if row.system == system]
    passes = [pass_cell(name, runs) for name in PASS_FAIL_EVALUATORS]
    tool_calls = [float(row.outcome.tool_calls) for row in runs]
    latencies = [row.outcome.latency_ms for row in runs]
    return [system, str(len(runs)), *passes, spread_cell(tool_calls), spread_cell(latencies)]


def pass_cell(evaluator_name: str, runs: Sequence[EvalRow]) -> str:
    passed = sum(1 for row in runs if row.evaluators[evaluator_name] is True)
    return f"{passed}/{len(runs)} {round(100 * passed / len(runs))}%"


def spread_cell(values: Sequence[float]) -> str:
    return f"{statistics.mean(values):.1f}/{min(values):.1f}/{max(values):.1f}"


def failure_lines(rows: Sequence[EvalRow]) -> list[str]:
    lines = []
    for row in rows:
        where = f"  {row.system} {row.scenario_id} repetition {row.repetition}"
        if row.outcome.outcome == "failed":
            lines.append(f"{where}: run failed ({row.outcome.failure})")
        failing = [name for name in PASS_FAIL_EVALUATORS if row.evaluators[name] is False]
        lines += [f"{where}: {name}" for name in failing]
    return ["failures:", *lines] if lines else ["failures: none"]


def elapsed_ms(started: float, finished: float) -> float:
    return max(finished - started, 0.0) * 1000
