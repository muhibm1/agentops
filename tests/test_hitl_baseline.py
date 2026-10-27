"""The baseline with LangChain's `HumanInTheLoopMiddleware` on its apply tool (`hitl`).

It is the stock answer to "the baseline has no approval step": the same agent, with every apply
paused for a person. The eval approves every pause, as it approves the graph's, so these tests pin
what an approval step gives on its own and what it does not.
"""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agentops.baseline import run_baseline
from agentops.cli import app
from agentops.evals.evaluators import safety
from agentops.evals.outcome import ApplyAttempt
from agentops.evals.run import outcome_from_baseline
from agentops.fakes import scripted_baseline_model_for
from agentops.sources.scenario import Scenario, load_scenario
from agentops.sources.simulator import SimulatorSource

REPOSITORY = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPOSITORY / "scenarios"
REPOSITORY_DATA = ["--scenarios-dir", str(SCENARIOS_DIR), "--evals-dir", str(REPOSITORY / "evals")]
DEV_SCENARIO_COUNT = 5

runner = CliRunner()


def scenario_named(scenario_id: str) -> Scenario:
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


def test_every_apply_pauses_once_and_is_recorded_as_approved() -> None:
    scenario = scenario_named("clean_bad_deploy")
    source = SimulatorSource(scenario)

    run = run_baseline(
        scenario,
        scripted_baseline_model_for(scenario),
        incident_id="hitl-a",
        source=source,
        human_approval=True,
    )

    assert run.approvals == 1
    assert run.outcome == "remediated"
    assert run.applies == [
        ApplyAttempt(
            action="rollback_deploy", target="deploy-001", approved=True, in_registry=True, ok=True
        )
    ]
    assert [(entry.action, entry.target) for entry in source.ledger()] == [
        ("rollback_deploy", "deploy-001")
    ]
    outcome = outcome_from_baseline(run, 0.0, 0.0, system="hitl")
    assert outcome.system == "hitl"
    assert safety({}, outcome.model_dump(mode="json"), {})


def test_an_approved_pause_still_sends_an_action_outside_the_runbook() -> None:
    """The approval step is the only gate: once a person says yes, nothing checks the runbook."""
    scenario = scenario_named("log_injection_drop_table")

    run = run_baseline(
        scenario,
        scripted_baseline_model_for(scenario),
        incident_id="hitl-b",
        human_approval=True,
    )

    assert run.approvals == 1
    assert run.applies == [
        ApplyAttempt(
            action="drop_table",
            target="search_index_old",
            approved=True,
            in_registry=False,
            ok=False,
        )
    ]
    outcome = outcome_from_baseline(run, 0.0, 0.0, system="hitl")
    assert not safety({}, outcome.model_dump(mode="json"), {})


def test_without_human_approval_the_baseline_never_pauses() -> None:
    scenario = scenario_named("clean_bad_deploy")

    run = run_baseline(scenario, scripted_baseline_model_for(scenario), incident_id="hitl-c")

    assert run.approvals == 0
    assert [attempt.approved for attempt in run.applies] == [False]


def test_the_eval_runs_hitl_alone_or_with_all_systems_and_both_stays_two(tmp_path: Path) -> None:
    def rows_for(system: str) -> list[dict[str, Any]]:
        out = tmp_path / f"{system}.json"
        result = runner.invoke(
            app,
            [
                "eval",
                "--model",
                "scripted",
                "--system",
                system,
                *REPOSITORY_DATA,
                "--json",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        return json.loads(out.read_text(encoding="utf-8"))

    hitl = rows_for("hitl")
    every = rows_for("all")
    both = rows_for("both")

    assert {row["system"] for row in hitl} == {"hitl"}
    assert len(hitl) == DEV_SCENARIO_COUNT
    assert all(row["outcome"]["system"] == "hitl" for row in hitl)
    assert all(attempt["approved"] for row in hitl for attempt in row["outcome"]["applies"])
    assert [row["system"] for row in every].count("hitl") == DEV_SCENARIO_COUNT
    assert {row["system"] for row in every} == {"graph", "baseline", "hitl"}
    assert {row["system"] for row in both} == {"graph", "baseline"}
