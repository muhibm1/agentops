"""`agentops eval` and `agentops dataset`, which `agentops.cli` registers on its app.

Exit codes follow the rest of the command line: 0 when the command completed (failed runs are
rows of the table, not a failed command); 1 when every run failed, or for a usage problem
(unknown model or system, repetitions outside 1 to 10, a tracing problem, a checksum mismatch,
a write without `--force`); 2 when the scenarios directory, a scenario file or the dataset files
cannot be read.
"""

import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from agentops.evals.dataset import (
    DatasetExistsError,
    Split,
    check_dataset,
    split_scenarios,
    write_dataset,
)
from agentops.evals.outcome import SystemName
from agentops.evals.run import (
    SYSTEMS,
    project_environment,
    render_header,
    render_table,
    rows_as_json,
    run_split,
)
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.tracing import tracing_status

USAGE_ERROR = 1
UNREADABLE = 2
MIN_REPETITIONS = 1
MAX_REPETITIONS = 10
BOTH_SYSTEMS = "both"
ERROR_TEXT_MAX_LENGTH = 200

ScenariosDirOption = Annotated[
    Path, typer.Option("--scenarios-dir", help="Directory holding the scenario files.")
]
EvalsDirOption = Annotated[
    Path,
    typer.Option("--evals-dir", help="Directory holding held_out/manifest.json and checksums."),
]


def eval_command(
    model: Annotated[str, typer.Option("--model", help="scripted (no API key) or anthropic.")],
    held_out: Annotated[
        bool,
        typer.Option(
            "--held-out",
            help="Score the frozen split, after checking its checksums. Only for a frozen version.",
        ),
    ] = False,
    repetitions: Annotated[
        int, typer.Option("--repetitions", help="Runs per system and scenario, 1 to 10.")
    ] = 1,
    system: Annotated[
        str, typer.Option("--system", help="graph, baseline or both.")
    ] = BOTH_SYSTEMS,
    json_path: Annotated[
        Path | None, typer.Option("--json", help="Also write one JSON row per run to this file.")
    ] = None,
    scenarios_dir: ScenariosDirOption = Path("scenarios"),
    evals_dir: EvalsDirOption = Path("evals"),
) -> None:
    """Score the graph and the ReAct baseline over the dev split, offline."""
    cli = command_line()
    cli.check_model_is_available(model)
    if not MIN_REPETITIONS <= repetitions <= MAX_REPETITIONS:
        stop(USAGE_ERROR, f"--repetitions must be {MIN_REPETITIONS} to {MAX_REPETITIONS}.")
    systems = systems_named(system)
    tracing = tracing_status()
    if tracing.problem is not None:
        stop(USAGE_ERROR, tracing.problem)
    require_readable_scenarios(scenarios_dir)
    split: Split = "held_out" if held_out else "dev"
    require_matching_checksums(scenarios_dir, evals_dir)
    scenarios = load_split(split, scenarios_dir, evals_dir)

    os.environ.update(project_environment(tracing))
    rows = run_split(
        scenarios,
        systems=systems,
        repetitions=repetitions,
        split=split,
        tracing=tracing,
        report=lambda text: typer.echo(cli.escape_for_terminal(text), err=True),
    )
    header = render_header(
        model=model,
        split=split,
        scenario_count=len(scenarios),
        repetitions=repetitions,
        tracing=tracing,
    )
    for line in [*header, *render_table(rows)]:
        typer.echo(cli.escape_for_terminal(line))
    if json_path is not None:
        write_json(json_path, rows_as_json(rows))
    if rows and all(row.outcome.outcome == "failed" for row in rows):
        stop(USAGE_ERROR, "Every run failed; the lines above say why.")


def dataset_command(
    write: Annotated[
        bool,
        typer.Option(
            "--write/--check",
            help="Write the manifest and checksums, or check them (the default).",
        ),
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Let --write overwrite the pinned split.")
    ] = False,
    scenarios_dir: ScenariosDirOption = Path("scenarios"),
    evals_dir: EvalsDirOption = Path("evals"),
) -> None:
    """Check the frozen split against its pinned sha256 checksums, or write them."""
    require_readable_scenarios(scenarios_dir)
    if not write:
        require_matching_checksums(scenarios_dir, evals_dir)
        typer.echo(f"The frozen split in {evals_dir} matches its pinned checksums.")
        return
    try:
        written = write_dataset(scenarios_dir, evals_dir, force=force)
    except DatasetExistsError as error:
        stop(USAGE_ERROR, str(error))
    except OSError as error:
        stop(UNREADABLE, f"The frozen split could not be written: {error}")
    for path in written:
        typer.echo(f"wrote {path}")


def command_line():
    """The `agentops.cli` module, imported when a command runs: `agentops.cli` imports this
    module to register the commands, so importing it at the top would be circular."""
    from agentops import cli

    return cli


def stop(exit_code: int, sentence: str) -> NoReturn:
    command_line().fail(exit_code, sentence)


def systems_named(system: str) -> list[SystemName]:
    if system == BOTH_SYSTEMS:
        return list(SYSTEMS)
    for name in SYSTEMS:
        if system == name:
            return [name]
    stop(USAGE_ERROR, f"Unknown system {system!r}; choose {', '.join(SYSTEMS)} or both.")


def require_readable_scenarios(scenarios_dir: Path) -> list[Scenario]:
    """Every scenario in the directory, loaded. Exits 2 naming the directory when it does not
    exist, or the first file that cannot be read or is not a valid scenario."""
    if not scenarios_dir.is_dir():
        stop(UNREADABLE, f"The scenarios directory {scenarios_dir} does not exist.")
    scenarios = []
    for scenario_id in list_scenario_ids(scenarios_dir):
        path = scenarios_dir / f"{scenario_id}.json"
        try:
            scenarios.append(load_scenario(path))
        except (OSError, UnicodeDecodeError, ValidationError) as error:
            stop(UNREADABLE, f"The scenario file {path} could not be read: {type(error).__name__}.")
    return scenarios


def require_matching_checksums(scenarios_dir: Path, evals_dir: Path) -> None:
    """Exits 1 with the first mismatch between the frozen split and its pinned checksums, and
    2 when the dataset files cannot be read."""
    try:
        problems = check_dataset(scenarios_dir, evals_dir)
    except (OSError, UnicodeDecodeError) as error:
        stop(
            UNREADABLE,
            f"The dataset files in {evals_dir} could not be read: {type(error).__name__}: {error}",
        )
    if problems:
        stop(USAGE_ERROR, f"The frozen split does not match its checksums: {problems[0]}")


def load_split(split: Split, scenarios_dir: Path, evals_dir: Path) -> list[Scenario]:
    """The scenarios of `split`. Exits 2 when the manifest cannot be read or names a missing
    scenario. Both splits are only asked for after the checksums matched: the manifest decides
    which scenarios are dev, so an edited manifest would move frozen ones into dev scoring."""
    try:
        return split_scenarios(split, scenarios_dir, evals_dir, allow_held_out=split == "held_out")
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        text = str(error)[:ERROR_TEXT_MAX_LENGTH]
        stop(
            UNREADABLE,
            f"The split could not be read from {scenarios_dir} and {evals_dir}: "
            f"{type(error).__name__}: {text}",
        )


def write_json(path: Path, text: str) -> None:
    try:
        path.write_text(text + "\n", encoding="utf-8")
    except OSError as error:
        stop(UNREADABLE, f"The JSON file {path} could not be written: {type(error).__name__}.")
