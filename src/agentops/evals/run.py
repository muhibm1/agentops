"""Run the graph and the ReAct baseline over a split, grade every run, and render the results.

A measurement writes nothing under the working directory: the simulator ledger is in memory, the
audit log and checkpoints live in a temporary directory, and every approval is automatic (role
`operator`). A run that raises is a `failed` row naming the exception type, not a crash.

Each run, with its judge call, is one repetition to the spend ladder (`agentops.budget`), so a
rerun replays that repetition's recorded answers. A refused paid call or a replay miss is not a
failed row: the measurement could not be made, so it stops the split.
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

import flywheel.spend as spend
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.baseline import BaselineRun, run_baseline
from agentops.evals.dataset import Split
from agentops.evals.evaluators import DETERMINISTIC_EVALUATORS, EvaluatorResult
from agentops.evals.judge import JUDGE_KEY, judge_with_usage
from agentops.evals.outcome import (
    UNDETERMINED_LABEL,
    RunOutcome,
    SystemName,
    TokenUsage,
    evidence_ids_of,
    inputs_for,
    outcome_from_state,
    reference_for,
    usage_by_model_of,
)
from agentops.graph import build_graph, checkpoint_serializer
from agentops.models import ModelFactory, ScriptedModels
from agentops.nodes import model_call_ledger
from agentops.pricing import Unpriced, cost_usd
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import Action, IncidentState, new_incident
from agentops.tracing import PROJECT_VARIABLE, TracingStatus

SYSTEMS: tuple[SystemName, ...] = get_args(SystemName)
RECURSION_LIMIT = 60
CHECKPOINT_FILE = "checkpoints.sqlite"
AUTO_APPROVAL = {"decision": "approve", "operator": "operator"}
FAILURE_MESSAGE_MAX_LENGTH = 200
# A scenario that loops needs one approval per action; a run still paused after this many fails.
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
SPEND_STOPS = spend.STOPS


@dataclass(frozen=True)
class EvalRow:
    """One run of one system over one scenario, with every evaluator's result."""

    system: SystemName
    scenario_id: str
    repetition: int
    outcome: RunOutcome
    evaluators: dict[str, EvaluatorResult]
    judge_call: Action | None = None  # priced apart from the run
    api: dict[str, Any] | None = None  # what the run billed (`spend.Meter.summary`)


def run_split(
    scenarios: Sequence[Scenario],
    *,
    systems: Sequence[SystemName],
    repetitions: int,
    split: Split,
    tracing: TracingStatus,
    report: Callable[[str], None] = lambda text: None,
    models: ModelFactory | None = None,
) -> list[EvalRow]:
    """Each system over each scenario, `repetitions` times, graded by the evaluators and, when
    `models` runs a judge, the judge on every graph run.

    `report` receives one line for every run or judge call that raised. Raises `SpendRefused` or
    `ReplayMiss` when the spend ladder stops a call."""
    models = models or ScriptedModels()
    rows: list[EvalRow] = []
    for system in systems:
        for scenario in scenarios:
            for repetition in range(1, repetitions + 1):
                config = trace_config(system, scenario.id, repetition, split, tracing)
                with spend.meter(sample=repetition) as used:
                    outcome = measured_run(system, scenario, config, report, models)
                    evaluators = graded(scenario, outcome)
                    judge_call = None
                    if system == "graph" and models.runs_judge:
                        judged, judge_call = judged_run(scenario, outcome, models, report)
                        if judged is not None:
                            evaluators[JUDGE_KEY] = judged
                rows.append(
                    EvalRow(
                        system=system,
                        scenario_id=scenario.id,
                        repetition=repetition,
                        outcome=outcome,
                        evaluators=evaluators,
                        judge_call=judge_call,
                        api=used.summary(),
                    )
                )
    return rows


def judged_run(
    scenario: Scenario,
    outcome: RunOutcome,
    models: ModelFactory,
    report: Callable[[str], None],
) -> tuple[dict[str, Any] | None, Action | None]:
    """The judge's result and call for one graph run; no result when the judge raised, unless
    the spend ladder stopped it."""
    try:
        return judge_with_usage(
            inputs_for(scenario),
            outcome.model_dump(mode="json"),
            reference_for(scenario),
            model=models.judge_model(),
        )
    except SPEND_STOPS:
        raise
    except Exception as error:  # a judge that raises is reported; the run's own row stands
        report(failure_summary("graph", scenario.id, error).replace("run failed", "judge failed"))
        return None, None


def run_graph_once(
    scenario: Scenario,
    *,
    config: RunnableConfig,
    models: ModelFactory | None = None,
    model_calls: Sequence[Action] = (),
) -> RunOutcome:
    """One graph run to its end, approving every pause. `model_calls` is the ledger of the run's
    model calls, so tries the graph retried are priced."""
    models = models or ScriptedModels()
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
                models.graph_models(scenario),  # type: ignore[arg-type]
                registry,
                AuditLog(Path(run_dir)),
                SqliteSaver(connection, serde=checkpoint_serializer()),
            )
            evidence_ids = evidence_ids_at_proposal(graph, incident, run_config)
            state = IncidentState(**graph.get_state(run_config).values)
    finished = time.perf_counter()
    return outcome_from_state(
        state, evidence_ids, started, finished, registry=registry, model_calls=model_calls
    )


def run_baseline_once(
    scenario: Scenario,
    *,
    config: RunnableConfig,
    models: ModelFactory | None = None,
    model_calls: Sequence[Action] = (),
) -> RunOutcome:
    """One baseline run over a fresh simulator; its usage comes from its own replies."""
    return run_baseline_system(scenario, config, models or ScriptedModels(), "baseline")


def run_hitl_once(
    scenario: Scenario,
    *,
    config: RunnableConfig,
    models: ModelFactory | None = None,
    model_calls: Sequence[Action] = (),
) -> RunOutcome:
    """The baseline with `HumanInTheLoopMiddleware` on its apply tool, approving every pause."""
    return run_baseline_system(scenario, config, models or ScriptedModels(), "hitl")


def run_baseline_system(
    scenario: Scenario, config: RunnableConfig, models: ModelFactory, system: SystemName
) -> RunOutcome:
    """Run the baseline agent, with human approval for `hitl`, reduced to a `RunOutcome`."""
    started = time.perf_counter()
    # One runnable, so a trace carries the run name and tags of `config`.
    baseline = RunnableLambda(
        lambda _: run_baseline(
            scenario,
            models.baseline_model(scenario),
            incident_id=f"eval-{scenario.id}",
            middleware=models.baseline_middleware(),
            human_approval=system == "hitl",
        )
    )
    run: BaselineRun = baseline.invoke(None, config)
    return outcome_from_baseline(run, started, time.perf_counter(), system=system)


RUNNERS: dict[SystemName, Callable[..., RunOutcome]] = {
    "graph": run_graph_once,
    "baseline": run_baseline_once,
    "hitl": run_hitl_once,
}


def measured_run(
    system: SystemName,
    scenario: Scenario,
    config: RunnableConfig,
    report: Callable[[str], None] = lambda text: None,
    models: ModelFactory | None = None,
) -> RunOutcome:
    """One run that never raises but for `SPEND_STOPS`; a failed run keeps the tokens it used."""
    models = models or ScriptedModels()
    started = time.perf_counter()
    with model_call_ledger() as calls:
        try:
            return RUNNERS[system](scenario, config=config, models=models, model_calls=calls)
        except SPEND_STOPS:
            raise
        except Exception as error:  # a run that raises is a measured failure, shown in the table
            report(failure_summary(system, scenario.id, error))
            failed = failed_outcome(system, scenario.id, error, started, time.perf_counter())
            return failed.model_copy(update={"usage_by_model": usage_by_model_of(calls)})


def failure_summary(system: SystemName, scenario_id: str, error: Exception) -> str:
    """One line naming the run, the exception and where it was raised; the table keeps only the
    type name."""
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
    """One line per system, the judge's own cost when it ran, then every failure."""
    judged = any(JUDGE_KEY in row.evaluators for row in rows)
    headings = [
        "system",
        "runs",
        *PASS_FAIL_EVALUATORS,
        *([JUDGE_KEY] if judged else []),
        "tool_calls mean/min/max",
        "latency_ms mean/min/max",
        *(f"{name} mean/min/max" for name in TOKEN_COLUMNS),
        "cost_usd mean/min/max",
    ]
    measured = {row.system for row in rows}
    table = [
        headings,
        *(summary_cells(system, rows, judged) for system in SYSTEMS if system in measured),
    ]
    widths = [max(len(line[column]) for line in table) for column in range(len(headings))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True))
        for line in table
    ]
    return [*(line.rstrip() for line in lines), *judge_cost_lines(rows), *failure_lines(rows)]


def rows_as_json(rows: Sequence[EvalRow]) -> str:
    """One object per run."""
    return json.dumps(
        [
            {
                "system": row.system,
                "scenario_id": row.scenario_id,
                "repetition": row.repetition,
                "outcome": row.outcome.model_dump(mode="json"),
                "evaluators": row.evaluators,
                "cost_usd": priced_or_none(cost_usd(row.outcome.usage_by_model)),
                "judge_call": (
                    row.judge_call.model_dump(mode="json") if row.judge_call is not None else None
                ),
                "api": row.api,
            }
            for row in rows
        ],
        indent=2,
    )


def evidence_ids_at_proposal(
    graph: CompiledStateGraph, incident: IncidentState, config: RunnableConfig
) -> list[str]:
    """Run the graph to its end, approving each pause, and return the evidence ids held when the
    run first proposed a remediation or escalated.

    Raises `RuntimeError` when the run never decides: its ids say nothing about evidence
    sufficiency, so it must become a failed row."""
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


def outcome_from_baseline(
    run: BaselineRun, started: float, finished: float, system: SystemName = "baseline"
) -> RunOutcome:
    proposed = [attempt.action for attempt in run.applies]
    if run.answer is not None and run.answer.action:
        proposed.append(run.answer.action)
    return RunOutcome(
        system=system,
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
        usage_by_model=run.usage_by_model,
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


def summary_cells(system: SystemName, rows: Sequence[EvalRow], judged: bool = False) -> list[str]:
    runs = [row for row in rows if row.system == system]
    passes = [pass_cell(name, runs) for name in PASS_FAIL_EVALUATORS]
    judge = [judge_cell(runs)] if judged else []
    tool_calls = [float(row.outcome.tool_calls) for row in runs]
    latencies = [row.outcome.latency_ms for row in runs]
    usages = [total_usage(row.outcome.usage_by_model) for row in runs]
    tokens = [
        integer_spread_cell([getattr(usage, name) for usage in usages]) for name in TOKEN_COLUMNS
    ]
    return [
        system,
        str(len(runs)),
        *passes,
        *judge,
        spread_cell(tool_calls),
        spread_cell(latencies),
        *tokens,
        cost_cell([cost_usd(row.outcome.usage_by_model) for row in runs]),
    ]


TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def total_usage(usage_by_model: dict[str, TokenUsage]) -> TokenUsage:
    total = TokenUsage()
    for usage in usage_by_model.values():
        total = total + usage
    return total


def judge_cell(runs: Sequence[EvalRow]) -> str:
    """Scores of 1 out of the runs the judge scored; `n/a` for a system it never scored."""
    scored = [row.evaluators[JUDGE_KEY] for row in runs if JUDGE_KEY in row.evaluators]
    if not scored:
        return "n/a"
    passed = sum(1 for result in scored if isinstance(result, dict) and result["score"] == 1)
    return f"{passed}/{len(scored)} {round(100 * passed / len(scored))}%"


def cost_cell(costs: Sequence[float | Unpriced]) -> str:
    """Dollars mean/min/max, or `n/a (unpriced: <id>)` when any model is unpriced: never a
    partial figure."""
    unpriced = next((cost for cost in costs if isinstance(cost, Unpriced)), None)
    if unpriced is not None:
        return f"n/a (unpriced: {unpriced.model_id})"
    priced = [cost for cost in costs if not isinstance(cost, Unpriced)]
    return f"${statistics.mean(priced):.2f}/${min(priced):.2f}/${max(priced):.2f}"


def judge_cost_lines(rows: Sequence[EvalRow]) -> list[str]:
    """The judge's cost, apart from the systems'; nothing when it never called."""
    calls = [row.judge_call for row in rows if row.judge_call is not None]
    if not calls:
        return []
    cost = cost_usd(usage_by_model_of(calls))
    figure = f"n/a (unpriced: {cost.model_id})" if isinstance(cost, Unpriced) else f"${cost:.2f}"
    return [f"judge cost: {figure} ({len(calls)} calls)"]


def priced_or_none(cost: float | Unpriced) -> float | None:
    return None if isinstance(cost, Unpriced) else cost


def integer_spread_cell(values: Sequence[int]) -> str:
    return f"{round(statistics.mean(values))}/{min(values)}/{max(values)}"


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
