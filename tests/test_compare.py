"""`agentops compare`: per-scenario churn between two eval result files (eval-methodology
principle 7), added 2026-10-12 so a change can be judged scenario by scenario, not by its mean."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agentops.cli import app
from agentops.evals.compare import compare, pass_counts, verdict_for

runner = CliRunner()


def row(system: str, scenario: str, repetition: int, **passes: bool) -> dict[str, Any]:
    evaluators = {
        name: passes.get(name, True)
        for name in (
            "root_cause_accuracy",
            "correct_outcome",
            "evidence_sufficiency",
            "false_remediation",
            "safety",
        )
    }
    return {
        "system": system,
        "scenario_id": scenario,
        "repetition": repetition,
        "outcome": {},
        "evaluators": evaluators,
        "cost_usd": None,
        "judge_call": None,
    }


BEFORE = [
    row("graph", "a", 1, correct_outcome=False),
    row("graph", "a", 2, correct_outcome=False),
    row("graph", "b", 1),
    row("graph", "c", 1, safety=False),
    row("graph", "d", 1),
    row("baseline", "a", 1, safety=False),
]
AFTER = [
    row("graph", "a", 1),
    row("graph", "a", 2, correct_outcome=False),
    row("graph", "b", 1, root_cause_accuracy=False),
    row("graph", "c", 1, root_cause_accuracy=False),
    row("graph", "d", 1),
    row("baseline", "a", 1, safety=False),
]


def test_each_scenario_gets_one_verdict_from_its_pass_rates() -> None:
    changes = {(c.system, c.scenario_id): c.verdict for c in compare(BEFORE, AFTER)}

    assert changes == {
        ("graph", "a"): "better",
        ("graph", "b"): "worse",
        ("graph", "c"): "mixed",
        ("graph", "d"): "same",
        ("baseline", "a"): "same",
    }


def test_pass_counts_sum_over_repetitions_and_compare_by_rate() -> None:
    before = pass_counts(BEFORE)[("graph", "a")]
    after = pass_counts([row("graph", "a", 1)])[("graph", "a")]

    assert before["correct_outcome"] == (0, 2)
    assert after["correct_outcome"] == (1, 1)
    assert verdict_for(before, after) == "better"


def test_the_command_prints_totals_and_what_moved(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(BEFORE), encoding="utf-8")
    after_path.write_text(json.dumps(AFTER), encoding="utf-8")

    result = runner.invoke(app, ["compare", str(before_path), str(after_path)])

    assert result.exit_code == 0, result.output
    assert "graph: 1 better, 1 worse, 1 mixed, 1 same" in result.output
    assert "correct_outcome: 3/5 -> 4/5" in result.output
    assert "better a: correct_outcome 0/2 -> 1/2" in result.output
    assert "mixed  c: root_cause_accuracy 1/1 -> 0/1, safety 0/1 -> 1/1" in result.output


def test_a_file_that_is_not_eval_output_exits_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "rows"}', encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(AFTER), encoding="utf-8")

    result = runner.invoke(app, ["compare", str(bad), str(good)])

    assert result.exit_code == 2
    assert "holds no runs" in result.output
