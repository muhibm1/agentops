"""The `agentops` command line: `--version`, and `run`, which investigates one scenario.

`run` checkpoints the thread to `agentops.sqlite` in the working directory, under a thread id
equal to the incident id, and writes the audit log and the simulator ledger under `audit/`. It
stops at the approval pause or at an escalation.

Exit codes for `run`: 0 when the graph pauses for approval or ends escalated, 1 for a usage problem
(unknown scenario, missing directory, invalid scenario file, model not available), 2 when the run
itself fails after the graph's retries. Both failures print one sentence to stderr naming the cause.
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from agentops import __version__
from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import scripted_model_for
from agentops.graph import build_graph, checkpoint_serializer
from agentops.sources.base import SIGNAL_ID_PATTERN
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.sources.simulator import SimulatorSource, ledger_path_for
from agentops.state import INITIAL_INVESTIGATION_BUDGET, IncidentState, new_incident

USAGE_ERROR = 1
RUN_FAILED = 2
RECURSION_LIMIT = 60
SCRIPTED_MODEL = "scripted"
LIVE_MODEL = "anthropic"
STATE_DB = Path("agentops.sqlite")
AUDIT_DIR = Path("audit")

app = typer.Typer(help="Investigate a simulated production incident with cited evidence.")


def print_version_and_exit(is_requested: bool) -> None:
    if is_requested:
        typer.echo(f"agentops {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=print_version_and_exit,
            is_eager=True,
            help="Print the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Investigate a simulated production incident with cited evidence."""


@app.command()
def run(
    scenario: Annotated[
        str, typer.Option("--scenario", help="Scenario id: a file stem in the scenarios directory.")
    ],
    model: Annotated[
        str, typer.Option("--model", help=f"{SCRIPTED_MODEL} (no API key) or {LIVE_MODEL}.")
    ],
    scenarios_dir: Annotated[
        Path, typer.Option("--scenarios-dir", help="Directory holding the scenario files.")
    ] = Path("scenarios"),
) -> None:
    """Investigate one scenario until the approval pause or an escalation."""
    check_model_is_available(model)
    loaded = load_named_scenario(scenario, scenarios_dir)
    incident = new_incident(
        loaded.service, loaded.symptoms, opened_at=loaded.incident_time, scenario_id=loaded.id
    )
    try:
        final_state, pending = run_to_pause(loaded, incident)
    except Exception as error:  # the command's outer boundary: any failure becomes exit 2
        fail(RUN_FAILED, f"The run failed: {type(error).__name__}: {error}")
    typer.echo(report(final_state, pending))


def run_to_pause(
    scenario: Scenario, incident: IncidentState
) -> tuple[IncidentState, dict[str, Any] | None]:
    """Run the thread named by the incident id until it pauses or ends, writing each checkpoint
    to `STATE_DB` before the next step starts. Returns the state and the pending approval
    payload, or None when the run did not pause."""
    thread_id = incident["incident_id"]
    source = SimulatorSource(scenario, ledger_path=ledger_path_for(AUDIT_DIR, thread_id))
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }
    with closing(sqlite3.connect(STATE_DB, check_same_thread=False)) as connection:
        graph = build_graph(
            source,
            scripted_model_for(scenario),
            default_registry(),
            AuditLog(AUDIT_DIR),
            SqliteSaver(connection, serde=checkpoint_serializer()),
        )
        result = graph.invoke(incident, config, durability="sync")
        final_state = IncidentState(**graph.get_state(config).values)
    interrupts = result.get("__interrupt__") or []
    return final_state, interrupts[0].value if interrupts else None


def check_model_is_available(model: str) -> None:
    if model == SCRIPTED_MODEL:
        return
    if model == LIVE_MODEL:
        fail(USAGE_ERROR, "The live Anthropic model is not wired yet; use --model scripted.")
    fail(USAGE_ERROR, f"Unknown model {model!r}; choose {SCRIPTED_MODEL} or {LIVE_MODEL}.")


def load_named_scenario(scenario_id: str, scenarios_dir: Path) -> Scenario:
    """Load `<scenarios_dir>/<scenario_id>.json`, refusing any id that is not a listed file stem.

    The id must match the scenario id pattern, so no path separator or `..` can reach the
    filesystem and only files inside `scenarios_dir` are ever opened.
    """
    if not scenarios_dir.is_dir():
        fail(USAGE_ERROR, f"The scenarios directory {scenarios_dir} does not exist.")
    available = list_scenario_ids(scenarios_dir)
    if not re.fullmatch(SIGNAL_ID_PATTERN, scenario_id) or scenario_id not in available:
        fail(
            USAGE_ERROR,
            f"Unknown scenario {scenario_id!r}; available: {', '.join(available) or 'none'}.",
        )
    path = scenarios_dir / f"{scenario_id}.json"
    try:
        return load_scenario(path)
    except (UnicodeDecodeError, OSError) as error:
        fail(USAGE_ERROR, f"The scenario file {path} could not be read: {type(error).__name__}.")
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "the file as a whole"
        fail(USAGE_ERROR, f"The scenario file {path} is invalid at {field}: {first['msg']}.")


def escape_for_terminal(text: str) -> str:
    """Show control characters (newline, ESC and the like) as visible escapes.

    Applied to every string a model wrote before it is printed, so model output cannot start a
    fake line or send terminal control sequences.
    """
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in text
    )


def report(state: IncidentState, pending: dict[str, Any] | None = None) -> str:
    """The run's outcome in plain lines, the pending proposal when the run is paused for
    approval, and where the audit log is. Every model string is escaped for the terminal."""
    lines = investigation_lines(state)
    if pending is not None:
        lines += pending_proposal_lines(pending)
    lines.append(f"audit: {AUDIT_DIR / (state['incident_id'] + '.jsonl')}")
    lines.append(closing_sentence(state))
    return "\n".join(lines)


def investigation_lines(state: IncidentState) -> list[str]:
    lines = [
        f"incident: {state['incident_id']}",
        f"thread: {state['incident_id']}",
        f"status: {state['status']}",
        f"loops: {state['loops']}",
        f"budget remaining: {state['investigation_budget']} of {INITIAL_INVESTIGATION_BUDGET}",
        "hypotheses:",
    ]
    lines += [
        f"  - {escape_for_terminal(item.name)} (score {item.score:.2f})"
        for item in state["hypotheses"]
    ]
    lines.append(f"evidence: {len(state['evidence'])} items")
    failed_actions = sum(1 for action in state["actions"] if not action.ok)
    lines.append(f"actions: {len(state['actions'])} ({failed_actions} failed)")
    if state["root_cause"] is not None:
        lines.append(f"root cause: {escape_for_terminal(state['root_cause'].label)}")
        lines.append(f"  {escape_for_terminal(state['root_cause'].explanation)}")
    if state["escalation_reason"] is not None:
        lines.append(f"escalation: {state['escalation_reason']}")
    if state["escalation"] is not None:
        lines.append(f"  {escape_for_terminal(state['escalation'].summary)}")
    if state["remediation"] is not None:
        remediation = state["remediation"]
        lines.append(
            f"remediation: {escape_for_terminal(remediation.action)} on "
            f"{escape_for_terminal(remediation.target)} (risk {remediation.risk})"
        )
    return lines


def pending_proposal_lines(pending: dict[str, Any]) -> list[str]:
    """The dry run and fingerprint of the proposal waiting for an operator's decision."""
    return [
        f"pending approval: {escape_for_terminal(str(pending.get('dry_run', '')))}",
        f"  idempotency key: {escape_for_terminal(str(pending.get('idempotency_key', '')))}",
    ]


def closing_sentence(state: IncidentState) -> str:
    """What happens next, and whether anything was changed so far."""
    execution = state["execution"]
    changes = (
        "An approved action was applied."
        if execution is not None and execution.applied
        else "Nothing was changed."
    )
    if state["status"] == "awaiting_approval":
        return f"Waiting for an operator's decision on thread {state['incident_id']}. {changes}"
    if state["status"] == "escalated":
        return f"Handed to a human. {changes}"
    return f"The run ended with status {state['status']}. {changes}"


def fail(exit_code: int, sentence: str) -> NoReturn:
    typer.echo(escape_for_terminal(sentence), err=True)
    raise typer.Exit(code=exit_code)
