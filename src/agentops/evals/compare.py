"""`agentops compare`: per-scenario churn between two `agentops eval --json` files.

A better mean can hide a regression (eval-methodology principle 7), so the comparison is per
system, per scenario and per pass-or-fail evaluator: each scenario is `better` when some
evaluator passed more often and none passed less, `worse` when the reverse, `mixed` when both,
and `same` otherwise. Pass counts are summed over repetitions, so files with different
repetition counts compare by rate.
"""

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

PASS_FAIL_EVALUATORS = (
    "root_cause_accuracy",
    "correct_outcome",
    "evidence_sufficiency",
    "false_remediation",
    "safety",
)
Verdict = Literal["better", "worse", "mixed", "same"]
Counts = dict[str, tuple[int, int]]  # evaluator -> (passes, runs)


class CompareError(ValueError):
    """A results file cannot be read as `agentops eval --json` output."""


@dataclass(frozen=True)
class ScenarioChange:
    system: str
    scenario_id: str
    verdict: Verdict
    before: Counts
    after: Counts


def load_rows(path: Path) -> list[dict[str, Any]]:
    """The rows of one `--json` file. Raises `CompareError` for anything but a list of run rows
    with a system, a scenario id and an evaluators mapping."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CompareError(
            f"{path} could not be read as eval output: {type(error).__name__}."
        ) from error
    if not isinstance(rows, list) or not rows:
        raise CompareError(f"{path} holds no runs.")
    for row in rows:
        if not (
            isinstance(row, dict)
            and isinstance(row.get("system"), str)
            and isinstance(row.get("scenario_id"), str)
            and isinstance(row.get("evaluators"), dict)
        ):
            raise CompareError(f"{path} holds a row that is not an eval run.")
    return rows


def pass_counts(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Counts]:
    """Passes and runs per (system, scenario) and evaluator, summed over repetitions. A failed
    run (an evaluator missing or not a boolean) counts as a run that did not pass."""
    counts: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
        lambda: {name: [0, 0] for name in PASS_FAIL_EVALUATORS}
    )
    for row in rows:
        key = (row["system"], row["scenario_id"])
        for name in PASS_FAIL_EVALUATORS:
            passed, runs = counts[key][name]
            counts[key][name] = [passed + (row["evaluators"].get(name) is True), runs + 1]
    return {
        key: {name: (passed, runs) for name, (passed, runs) in per_evaluator.items()}
        for key, per_evaluator in counts.items()
    }


def rate(count: tuple[int, int]) -> float:
    passed, runs = count
    return passed / runs if runs else 0.0


def verdict_for(before: Counts, after: Counts) -> Verdict:
    """`better`, `worse`, `mixed` or `same` from the per-evaluator pass rates."""
    up = any(rate(after[name]) > rate(before[name]) for name in PASS_FAIL_EVALUATORS)
    down = any(rate(after[name]) < rate(before[name]) for name in PASS_FAIL_EVALUATORS)
    if up and down:
        return "mixed"
    if up:
        return "better"
    if down:
        return "worse"
    return "same"


def compare(
    before_rows: Sequence[Mapping[str, Any]], after_rows: Sequence[Mapping[str, Any]]
) -> list[ScenarioChange]:
    """One change per (system, scenario) present in both files, systems then scenarios in
    name order. A scenario in only one file is left out: there is nothing to compare it with."""
    before = pass_counts(before_rows)
    after = pass_counts(after_rows)
    return [
        ScenarioChange(
            system, scenario_id, verdict_for(before[key], after[key]), before[key], after[key]
        )
        for key in sorted(before)
        if key in after
        for system, scenario_id in [key]
    ]


def render(changes: Sequence[ScenarioChange]) -> str:
    """The per-system totals, then one line per scenario naming what moved."""
    lines: list[str] = []
    for system in sorted({change.system for change in changes}):
        own = [change for change in changes if change.system == system]
        tally = {
            verdict: sum(1 for c in own if c.verdict == verdict)
            for verdict in ("better", "worse", "mixed", "same")
        }
        lines.append(
            f"{system}: {tally['better']} better, {tally['worse']} worse, {tally['mixed']} mixed, "
            f"{tally['same']} same"
        )
        for name in PASS_FAIL_EVALUATORS:
            before_passed = sum(c.before[name][0] for c in own)
            before_runs = sum(c.before[name][1] for c in own)
            after_passed = sum(c.after[name][0] for c in own)
            after_runs = sum(c.after[name][1] for c in own)
            lines.append(f"  {name}: {before_passed}/{before_runs} -> {after_passed}/{after_runs}")
        for change in own:
            moved = [
                f"{name} {change.before[name][0]}/{change.before[name][1]}"
                f" -> {change.after[name][0]}/{change.after[name][1]}"
                for name in PASS_FAIL_EVALUATORS
                if rate(change.after[name]) != rate(change.before[name])
            ]
            detail = f": {', '.join(moved)}" if moved else ""
            lines.append(f"  {change.verdict:6} {change.scenario_id}{detail}")
    return "\n".join(lines)


def compare_command(
    before: Annotated[Path, typer.Argument(help="An earlier `agentops eval --json` file.")],
    after: Annotated[Path, typer.Argument(help="A later `agentops eval --json` file.")],
) -> None:
    """Compare two eval result files scenario by scenario: better, worse, mixed or same."""
    from agentops.evals.commands import UNREADABLE, stop

    try:
        changes = compare(load_rows(before), load_rows(after))
    except CompareError as error:
        stop(UNREADABLE, str(error))
    if not changes:
        stop(UNREADABLE, "The two files share no system and scenario to compare.")
    typer.echo(render(changes))
