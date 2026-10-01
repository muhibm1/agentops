"""The `agentops` command line: `--version`, and `run`, which investigates one scenario.

Exit codes for `run`: 0 when the graph reaches a root cause, 1 for a usage problem (unknown
scenario, missing directory, invalid scenario file, model not available), 2 when the run itself
fails after the graph's retries. Both failures print one sentence to stderr naming the cause.
"""

import re
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from agentops import __version__
from agentops.fakes import ScriptedChatModel
from agentops.graph import MODEL_NODE_RETRY, build_graph
from agentops.sources.base import SIGNAL_ID_PATTERN
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import IncidentState, new_incident

USAGE_ERROR = 1
RUN_FAILED = 2
SCRIPTED_MODEL = "scripted"
LIVE_MODEL = "anthropic"

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
    """Investigate one scenario through triage, evidence and root-cause analysis."""
    check_model_is_available(model)
    loaded = load_named_scenario(scenario, scenarios_dir)
    graph = build_graph(SimulatorSource(loaded), scripted_model_with_retries(loaded))
    incident = new_incident(
        loaded.service, loaded.symptoms, opened_at=loaded.incident_time, scenario_id=loaded.id
    )
    try:
        final_state = graph.invoke(incident)
    except Exception as error:  # the command's outer boundary: any failure becomes exit 2
        fail(RUN_FAILED, f"The run failed: {type(error).__name__}: {error}")
    typer.echo(report(IncidentState(**final_state)))


def check_model_is_available(model: str) -> None:
    if model == SCRIPTED_MODEL:
        return
    if model == LIVE_MODEL:
        fail(USAGE_ERROR, "The live Anthropic model arrives in increment 2; use --model scripted.")
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
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "the file as a whole"
        fail(USAGE_ERROR, f"The scenario file {path} is invalid at {field}: {first['msg']}.")


def scripted_model_with_retries(scenario: Scenario) -> ScriptedChatModel:
    """Triage's answer once, then the rca answer once per attempt the graph allows.

    A rejected rca answer is retried, and the retry must meet the same answer again so the run
    fails with the real cause (for example `CitationError`) rather than an exhausted script.
    """
    outputs = scenario.scripted_outputs
    return ScriptedChatModel(
        script=[outputs.triage, *[outputs.rca] * MODEL_NODE_RETRY.max_attempts]
    )


def report(state: IncidentState) -> str:
    lines = [f"incident: {state['incident_id']}", f"status: {state['status']}", "hypotheses:"]
    lines += [f"  - {item.name} (score {item.score:.2f})" for item in state["hypotheses"]]
    lines.append(f"evidence: {len(state['evidence'])} items")
    if state["root_cause"] is not None:
        lines.append(f"root cause: {state['root_cause'].label}")
        lines.append(f"  {state['root_cause'].explanation}")
    if state["remediation"] is not None:
        remediation = state["remediation"]
        lines.append(
            f"remediation: {remediation.action} on {remediation.target} (risk {remediation.risk})"
        )
    lines.append("Approval and execution arrive in increment 3; nothing was changed.")
    return "\n".join(lines)


def fail(exit_code: int, sentence: str) -> NoReturn:
    typer.echo(sentence, err=True)
    raise typer.Exit(code=exit_code)
