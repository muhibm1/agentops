"""`--watch`: the thread commands stream the run, one line per graph step, and end in the same
state as without it."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentops.cli import app
from agentops.state import (
    RCA,
    Approval,
    Evidence,
    ExecutionRecord,
    Hypothesis,
    Remediation,
)
from agentops.watch import INTERRUPT_KEY, step_line

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
NOW = datetime(2026, 11, 5, tzinfo=UTC)
runner = CliRunner()


def invoke(tmp_path: Path, *args: str) -> str:
    """Run one command over a checkpoint file and audit directory under `tmp_path`."""
    result = runner.invoke(
        app,
        [
            *args,
            *(["--model", "scripted"] if args[0] == "run" else []),
            "--state-db",
            str(tmp_path / "agentops.sqlite"),
            "--audit-dir",
            str(tmp_path / "audit"),
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
    )
    assert result.exit_code == 0, result.output
    return result.output


def evidence(source: str) -> Evidence:
    """One evidence item read from `source`."""
    return Evidence(id=f"e-{source}", source=source, kind="log", payload={}, collected_at=NOW)


def remediation() -> Remediation:
    """A rollback of the checkout service."""
    return Remediation(
        action="rollback_deploy",
        target="checkout",
        params={},
        reason="the deploy broke it",
        evidence_ids=["e-get_logs"],
        risk="low",
        rollback="redeploy",
    )


def report_facts(output: str) -> list[str]:
    """The report's lines that do not depend on the thread id."""
    report = output[output.index("incident: ") :].splitlines()
    return [
        line
        for line in report
        if not line.startswith(("incident:", "thread:", "  idem", "audit:", "Waiting"))
    ]


class TestStepLine:
    def test_an_interrupt_reads_as_the_approval_pause(self) -> None:
        assert step_line(INTERRUPT_KEY, ()) == "approval: paused for a person's decision"

    def test_an_update_that_is_not_a_mapping_reads_as_done(self) -> None:
        assert step_line("merge", None) == "merge: done"

    def test_an_empty_update_reads_as_no_change(self) -> None:
        assert step_line("merge", {}) == "merge: no change"

    def test_evidence_is_counted_and_its_sources_named_once_each(self) -> None:
        update = {"evidence": [evidence("get_metrics"), evidence("get_logs"), evidence("get_logs")]}
        assert step_line("logs", update) == "logs: +3 evidence from get_logs, get_metrics"

    def test_hypotheses_name_the_top_two_best_first(self) -> None:
        update = {
            "hypotheses": [
                Hypothesis(name="dependency_timeout", score=0.2),
                Hypothesis(name="database_saturation", score=0.9),
                Hypothesis(name="recent_deploy_regression", score=0.5),
            ]
        }
        assert step_line("rca", update) == (
            "rca: top database_saturation (0.90), recent_deploy_regression (0.50)"
        )

    def test_a_diagnosis_names_its_cause_and_proposed_action(self) -> None:
        update = {
            "root_cause": RCA(
                label="recent_deploy_regression", explanation="x", evidence_ids=["e"]
            ),
            "remediation": remediation(),
            "status": "awaiting_approval",
        }
        assert step_line("rca", update) == (
            "rca: cause recent_deploy_regression; proposes rollback_deploy on checkout; "
            "status awaiting_approval"
        )

    def test_a_decision_names_who_decided_what(self) -> None:
        approval = Approval(
            decision="approve", operator="on-call", decided_at=NOW, idempotency_key="k"
        )
        assert step_line("approval", {"approval": approval}) == (
            "approval: decision approve by on-call"
        )

    @pytest.mark.parametrize(
        ("applied", "word"),
        [(True, "applied"), (False, "not applied"), (None, "effect unknown")],
    )
    def test_an_execution_says_whether_it_applied(self, applied: bool | None, word: str) -> None:
        record = ExecutionRecord(
            idempotency_key="k",
            action="rollback_deploy",
            target="checkout",
            applied=applied,
            result="ok",
            dry_run_output="would roll back",
        )
        assert step_line("execute", {"execution": record}) == f"execute: rollback_deploy {word}"

    def test_budget_loop_and_escalation_are_reported(self) -> None:
        update = {"investigation_budget": 0, "loops": 2, "escalation_reason": "budget_exhausted"}
        assert step_line("merge", update) == (
            "merge: escalation reason budget_exhausted; budget 0 left; loop 2"
        )

    def test_dict_shaped_items_read_like_models(self) -> None:
        update = {"hypotheses": [{"name": "database_saturation", "score": 0.7}]}
        assert step_line("triage", update) == "triage: top database_saturation (0.70)"


class TestWatchedCommands:
    def test_run_prints_each_step_in_order_then_the_same_report(self, tmp_path: Path) -> None:
        watched = invoke(
            tmp_path, "run", "--scenario", "deploy_red_herring_db", "--thread", "w", "--watch"
        )
        plain = invoke(tmp_path, "run", "--scenario", "deploy_red_herring_db", "--thread", "p")
        lines = watched.splitlines()
        assert lines[0].startswith("triage: top recent_deploy_regression (0.50)")
        rca = next(index for index, line in enumerate(lines) if line.startswith("rca: "))
        assert "cause database_saturation" in lines[rca]
        assert "proposes scale_db_pool on postgres" in lines[rca]
        assert lines[rca + 1] == "approval: paused for a person's decision"
        assert {line.split(":")[0] for line in lines[1:rca]} >= {"logs", "metrics", "deploys"}
        # After the step lines comes the report a plain run prints, the same apart from ids.
        assert report_facts(watched) == report_facts(plain)

    def test_approve_streams_the_decision_the_apply_and_the_postmortem(
        self, tmp_path: Path
    ) -> None:
        invoke(tmp_path, "run", "--scenario", "clean_bad_deploy", "--thread", "t")
        output = invoke(tmp_path, "approve", "t", "--decision", "approve", "--watch")
        steps = [line.split(":")[0] for line in output.splitlines()[:4]]
        assert steps == ["approval", "execute", "verify", "postmortem"]
        assert "decision approve by operator" in output.splitlines()[0]
        assert "rollback_deploy applied" in output.splitlines()[1]
        assert "status: resolved" in output

    def test_an_escalation_is_watched_to_its_end(self, tmp_path: Path) -> None:
        output = invoke(
            tmp_path, "run", "--scenario", "ambiguous_two_causes", "--thread", "a", "--watch"
        )
        lines = output.splitlines()
        escalate = next(line for line in lines if line.startswith("escalate: "))
        assert "status escalated" in escalate
        assert "status: escalated" in output
