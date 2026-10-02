"""E18, E19, E29 to E31, E43, E44, E60 to E62 and N3: the thread commands of the command line,
`run`, `approve`, `resume`, `history` and `fork`, over a SQLite checkpoint file (R18, R19).

Every test keeps its checkpoint file and audit directory under its own temporary directory. E18
runs the commands as separate processes, so a crash between apply and the end of the execute node
is a real process exit.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from agentops.cli import app

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
CLEAN_BAD_DEPLOY = "clean_bad_deploy"
RED_HERRING = "deploy_red_herring_db"
LOG_INJECTION = "log_injection_drop_table"
TWO_FAULTS = "two_concurrent_faults"
# N3: `run` then `approve` of scenario 1 through the command line, together.
RUN_AND_APPROVE_SECONDS = 10
HISTORY_LINE = re.compile(
    r"^(?P<id>\S+) (?P<created>\S+) next: (?P<next>\S+) status: (?P<status>\S+) "
    r"loops: (?P<loops>\d+) top: (?P<top>.*) evidence: (?P<evidence>\d+)$"
)
ANSI_RED = "\x1b[31m"

runner = CliRunner()


@dataclass(frozen=True)
class Workspace:
    """Where one test keeps its checkpoint file, audit directory and scenarios."""

    state_db: Path
    audit_dir: Path
    scenarios_dir: Path = SCENARIOS_DIR

    def options(self) -> list[str]:
        return [
            "--state-db",
            str(self.state_db),
            "--audit-dir",
            str(self.audit_dir),
            "--scenarios-dir",
            str(self.scenarios_dir),
        ]

    def ledger(self, incident_id: str) -> list[dict[str, Any]]:
        return read_lines(self.audit_dir / f"{incident_id}.ledger.jsonl")

    def audit(self, incident_id: str) -> list[dict[str, Any]]:
        return read_lines(self.audit_dir / f"{incident_id}.jsonl")


def read_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(state_db=tmp_path / "agentops.sqlite", audit_dir=tmp_path / "audit")


def agentops(workspace: Workspace, *arguments: str) -> Result:
    return runner.invoke(app, [*arguments, *workspace.options()])


def run_scenario(workspace: Workspace, scenario_id: str, thread: str) -> Result:
    return agentops(
        workspace, "run", "--scenario", scenario_id, "--model", "scripted", "--thread", thread
    )


def history_lines(workspace: Workspace, thread: str) -> list[re.Match[str]]:
    result = agentops(workspace, "history", thread)
    assert result.exit_code == 0, result.output
    matches = [HISTORY_LINE.match(line) for line in result.stdout.splitlines()]
    assert all(matches), result.stdout
    return [match for match in matches if match is not None]


def process(workspace: Workspace, *arguments: str) -> subprocess.CompletedProcess[str]:
    """One `agentops` command as its own process, so a crash is a real process exit."""
    return subprocess.run(
        [sys.executable, "-m", "agentops", *arguments, *workspace.options()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
        check=False,
    )


# E18 and N3: a process killed after apply, then resumed in a new process, applies nothing again.
def test_a_crash_after_apply_resumes_in_a_new_process_without_a_second_apply(
    workspace: Workspace,
) -> None:
    thread = "crash-demo"

    def agentops_process(*arguments: str) -> subprocess.CompletedProcess[str]:
        return process(workspace, *arguments)

    started = time.perf_counter()
    first = agentops_process(
        "run", "--scenario", CLEAN_BAD_DEPLOY, "--model", "scripted", "--thread", thread
    )
    second = agentops_process("approve", thread, "--decision", "approve", "--crash-after-apply")
    run_and_approve_seconds = time.perf_counter() - started
    ledger_after_crash = workspace.ledger(thread)
    third = agentops_process("resume", thread)
    total_seconds = time.perf_counter() - started
    print(f"N3: run and approve {run_and_approve_seconds:.2f} s; with resume {total_seconds:.2f} s")

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert len(ledger_after_crash) == 1
    assert third.returncode == 0, third.stderr
    assert "status: resolved" in third.stdout
    assert len(workspace.ledger(thread)) == 1
    applied = [
        record
        for record in workspace.audit(thread)
        if record["event"] == "apply" and record["applied"] is True
    ]
    assert len(applied) == 1
    assert run_and_approve_seconds < RUN_AND_APPROVE_SECONDS


# E18 on the loop scenario: the first action is applied and the process dies, resume must still
# see it as applied, so verify does not clear and the run loops to the second action.
def test_a_crash_after_the_first_action_of_two_faults_resumes_into_the_loop_and_resolves(
    workspace: Workspace,
) -> None:
    thread = "crash-two-faults"

    first = process(
        workspace, "run", "--scenario", TWO_FAULTS, "--model", "scripted", "--thread", thread
    )
    crashed = process(workspace, "approve", thread, "--decision", "approve", "--crash-after-apply")
    resumed = process(workspace, "resume", thread)
    final = process(workspace, "approve", thread, "--decision", "approve")

    assert first.returncode == 0, first.stderr
    assert crashed.returncode != 0
    assert resumed.returncode == 0, resumed.stderr
    assert "escalation" not in resumed.stdout
    assert "remediation: scale_db_pool" in resumed.stdout
    assert "An approved action was applied." in resumed.stdout
    assert final.returncode == 0, final.stderr
    assert "status: resolved" in final.stdout
    assert [entry["action"] for entry in workspace.ledger(thread)] == [
        "rollback_deploy",
        "scale_db_pool",
    ]
    applied = [
        record["action"]
        for record in workspace.audit(thread)
        if record["event"] == "apply" and record["applied"] is True
    ]
    assert applied == ["rollback_deploy", "scale_db_pool"]
    assert "Actions already applied: rollback_deploy" not in final.stdout


# E19: run pauses at approval and prints the proposal; approve runs it to the end.
def test_run_then_approve_resolves_scenario_one(workspace: Workspace) -> None:
    started = run_scenario(workspace, CLEAN_BAD_DEPLOY, "incident-1")
    approved = agentops(workspace, "approve", "incident-1", "--decision", "approve")
    live_model = agentops(
        workspace, "approve", "incident-1", "--decision", "approve", "--model", "anthropic"
    )

    assert started.exit_code == 0, started.output
    assert "thread: incident-1" in started.stdout
    assert "pending approval: Would apply rollback_deploy" in started.stdout
    assert approved.exit_code == 0, approved.output
    assert "status: resolved" in approved.stdout
    assert "approval: approve by operator" in approved.stdout
    assert "execution: applied" in approved.stdout
    assert len(workspace.ledger("incident-1")) == 1
    assert live_model.exit_code == 1
    assert "not wired yet" in live_model.stderr


# E29: one line per checkpoint, newest first; the newest waits for approval.
def test_history_lists_each_checkpoint_newest_first(workspace: Workspace) -> None:
    run_scenario(workspace, CLEAN_BAD_DEPLOY, "incident-1")

    lines = history_lines(workspace, "incident-1")

    created = [line["created"] for line in lines]
    assert len(lines) >= 3
    assert created == sorted(created, reverse=True)
    assert lines[0]["next"] == "approval"
    assert lines[0]["status"] == "awaiting_approval"
    assert int(lines[0]["evidence"]) > 0
    assert "recent_deploy_regression" in lines[0]["top"]


# E30: a fork starts a new thread from a past checkpoint with a changed score.
def test_fork_continues_a_new_thread_and_leaves_the_original_history_intact(
    workspace: Workspace,
) -> None:
    run_scenario(workspace, RED_HERRING, "red-1")
    original = [line.group(0) for line in history_lines(workspace, "red-1")]
    after_triage = next(
        line for line in reversed(history_lines(workspace, "red-1")) if "metrics" in line["next"]
    )

    forked = agentops(
        workspace, "fork", "red-1", "--at", after_triage["id"], "--set", "database_saturation=0.9"
    )

    assert forked.exit_code == 0, forked.output
    assert "thread: red-1-fork-1" in forked.stdout
    assert re.search(r"status: (awaiting_approval|escalated)", forked.stdout)
    fork_history = history_lines(workspace, "red-1-fork-1")
    assert any("database_saturation (0.90)" in line["top"] for line in fork_history)
    assert [line.group(0) for line in history_lines(workspace, "red-1")] == original


# A fork explores a counterfactual: it must not inherit the original's applied actions.
def test_a_fork_after_an_apply_has_its_own_empty_ledger_and_applies_for_itself(
    workspace: Workspace,
) -> None:
    run_scenario(workspace, CLEAN_BAD_DEPLOY, "incident-1")
    after_triage = next(
        line
        for line in reversed(history_lines(workspace, "incident-1"))
        if "metrics" in line["next"]
    )
    agentops(workspace, "approve", "incident-1", "--decision", "approve")

    forked = agentops(
        workspace,
        "fork",
        "incident-1",
        "--at",
        after_triage["id"],
        "--set",
        "recent_deploy_regression=0.9",
    )
    fork_ledger_before_approval = workspace.ledger("incident-1-fork-1")
    approved = agentops(workspace, "approve", "incident-1-fork-1", "--decision", "approve")

    assert forked.exit_code == 0, forked.output
    assert "pending approval: Would apply rollback_deploy" in forked.stdout
    assert fork_ledger_before_approval == []
    assert approved.exit_code == 0, approved.output
    assert "execution: applied rollback_deploy" in approved.stdout
    assert len(workspace.ledger("incident-1-fork-1")) == 1
    assert len(workspace.ledger("incident-1")) == 1


# An answer the node refuses re-asks the question, and the command must say it failed.
def test_approve_with_an_edit_the_node_refuses_exits_one_and_applies_nothing(
    workspace: Workspace, tmp_path: Path
) -> None:
    run_scenario(workspace, CLEAN_BAD_DEPLOY, "incident-1")
    edit = tmp_path / "edit.json"
    edit.write_text(
        json.dumps(
            {
                "action": "drop_table",
                "target": "orders",
                "params": {},
                "reason": "x",
                "evidence_ids": ["log-001"],
                "risk": "low",
                "rollback": "none",
            }
        ),
        encoding="utf-8",
    )

    refused = agentops(
        workspace,
        "approve",
        "incident-1",
        "--decision",
        "edit",
        "--remediation-file",
        str(edit),
    )

    assert refused.exit_code == 1
    assert "last answer refused" in refused.stdout
    assert "was refused" in refused.stderr
    assert workspace.ledger("incident-1") == []


# E31: resume on a thread that waits for a decision only shows the decision to make.
def test_resume_on_a_waiting_thread_prints_the_proposal_and_applies_nothing(
    workspace: Workspace,
) -> None:
    run_scenario(workspace, RED_HERRING, "red-1")

    resumed = agentops(workspace, "resume", "red-1")

    assert resumed.exit_code == 0, resumed.output
    assert "pending approval: Would apply scale_db_pool" in resumed.stdout
    assert "agentops approve red-1" in resumed.stdout
    assert workspace.ledger("red-1") == []


# E43: an unknown or malformed thread is a usage error, and touches no audit file.
def test_unknown_and_malformed_threads_exit_one_before_any_file_is_written(
    workspace: Workspace,
) -> None:
    results = [
        agentops(workspace, "resume", "no-such-thread"),
        agentops(workspace, "approve", "no-such-thread", "--decision", "approve"),
        agentops(workspace, "history", "no-such-thread"),
    ]
    traversal = run_scenario(workspace, CLEAN_BAD_DEPLOY, "../x")

    for result in results:
        assert result.exit_code == 1
        assert "no-such-thread" in result.stderr
    assert traversal.exit_code == 1
    assert "../x" in traversal.stderr
    assert not workspace.audit_dir.exists()
    assert not workspace.state_db.exists()


# E44: a resolved thread is not waiting for a decision.
def test_approve_on_a_resolved_thread_exits_one_and_applies_nothing_more(
    workspace: Workspace,
) -> None:
    run_scenario(workspace, CLEAN_BAD_DEPLOY, "incident-1")
    agentops(workspace, "approve", "incident-1", "--decision", "approve")

    again = agentops(workspace, "approve", "incident-1", "--decision", "approve")

    assert again.exit_code == 1
    assert "not waiting for approval" in again.stderr
    assert len(workspace.ledger("incident-1")) == 1


# E60: a bad decision or an unknown hypothesis is a usage error naming the value.
def test_a_bad_decision_and_an_unknown_hypothesis_exit_one_naming_the_value(
    workspace: Workspace,
) -> None:
    run_scenario(workspace, RED_HERRING, "red-1")
    before = [line.group(0) for line in history_lines(workspace, "red-1")]
    checkpoint = history_lines(workspace, "red-1")[0]["id"]

    bad_decision = agentops(workspace, "approve", "red-1", "--decision", "maybe")
    bad_hypothesis = agentops(
        workspace, "fork", "red-1", "--at", checkpoint, "--set", "unknown_hypothesis=0.9"
    )

    assert bad_decision.exit_code == 1
    assert "maybe" in bad_decision.stderr
    assert bad_hypothesis.exit_code == 1
    assert "unknown_hypothesis" in bad_hypothesis.stderr
    assert workspace.ledger("red-1") == []
    assert [line.group(0) for line in history_lines(workspace, "red-1")] == before


def test_a_thread_whose_checkpoint_holds_no_incident_exits_one_with_a_sentence(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    class EmptySaver:
        def get_tuple(self, config: Any) -> SimpleNamespace:
            return SimpleNamespace(checkpoint={"channel_values": {}})

    @contextmanager
    def empty_checkpoints(state_db: Path) -> Iterator[EmptySaver]:
        yield EmptySaver()

    workspace.state_db.write_text("", encoding="utf-8")
    monkeypatch.setattr("agentops.cli.open_checkpoints", empty_checkpoints)

    result = agentops(workspace, "history", "empty-1")

    assert result.exit_code == 1
    assert "empty-1" in result.stderr
    assert "no usable checkpoint" in result.stderr
    assert "Traceback" not in result.output


# E61: a checkpoint path that cannot be opened is a failed run, before anything is written.
def test_an_unusable_state_database_exits_two_naming_the_path(tmp_path: Path) -> None:
    directory_as_db = tmp_path / "state-is-a-directory"
    directory_as_db.mkdir()
    workspace = Workspace(state_db=directory_as_db, audit_dir=tmp_path / "audit")

    started = run_scenario(workspace, CLEAN_BAD_DEPLOY, "incident-1")
    approved = agentops(workspace, "approve", "incident-1", "--decision", "approve")

    for result in (started, approved):
        assert result.exit_code == 2
        assert str(directory_as_db) in result.stderr
    assert not workspace.audit_dir.exists()


# E62: escape bytes in evidence or in a reject reason never reach the terminal raw.
def test_ansi_escapes_never_reach_the_terminal(tmp_path: Path) -> None:
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    document = json.loads((SCENARIOS_DIR / f"{LOG_INJECTION}.json").read_text(encoding="utf-8"))
    for line in document["signals"]["logs"]:
        line["message"] = f"{ANSI_RED}[bold]{line['message']}[/bold]\x1b[0m"
    (scenarios_dir / f"{LOG_INJECTION}.json").write_text(json.dumps(document), encoding="utf-8")
    workspace = Workspace(
        state_db=tmp_path / "agentops.sqlite",
        audit_dir=tmp_path / "audit",
        scenarios_dir=scenarios_dir,
    )

    results = [
        run_scenario(workspace, LOG_INJECTION, "inject-1"),
        agentops(
            workspace,
            "approve",
            "inject-1",
            "--decision",
            "reject",
            "--reason",
            f"{ANSI_RED}not now\x1b[2J",
        ),
        agentops(workspace, "history", "inject-1"),
    ]

    for result in results:
        assert result.exit_code == 0, result.output
        assert "\x1b" not in result.stdout
        assert "\x1b" not in result.stderr


# E40 (R4): a ledger torn by a crash mid-write is reported once on stderr, and the approved
# action still applies exactly once, on a fresh line after the fragment.
def test_a_torn_ledger_line_is_reported_and_the_action_still_applies_once(
    workspace: Workspace,
) -> None:
    thread = "torn-1"
    fragment = '{"idempotency_key": "ab'
    run_scenario(workspace, CLEAN_BAD_DEPLOY, thread)
    ledger_path = workspace.audit_dir / f"{thread}.ledger.jsonl"
    ledger_path.write_text(fragment, encoding="utf-8")

    approved = agentops(workspace, "approve", thread, "--decision", "approve")

    assert approved.exit_code == 0, approved.output
    assert f"ledger: skipped torn line 1 in {ledger_path}" in approved.stderr
    assert "status: resolved" in approved.stdout
    ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert ledger_lines[0] == fragment
    assert ledger_lines[1] == '{"torn_seal": true}'
    assert [json.loads(line)["action"] for line in ledger_lines[2:]] == ["rollback_deploy"]
    applied = [
        record
        for record in workspace.audit(thread)
        if record["event"] == "apply" and record["applied"] is True
    ]
    assert len(applied) == 1
    # The sealed fragment is no longer the last line; a later process must still read the thread.
    resumed = agentops(workspace, "resume", thread)
    history = agentops(workspace, "history", thread)
    assert resumed.exit_code == 0, resumed.output
    assert history.exit_code == 0, history.output


# R4 second round: a second crash inside the repair write (a torn seal marker) must leave a
# thread that still resumes and shows its history, and a ledger whose last entry lost only its
# newline must not be sealed as if it were a fragment.
@pytest.mark.parametrize(
    "tail",
    ['{"idempotency_key": "ab\n{"torn_se', '{"idempotency_key": "ab\n{"torn_seal": true}\n{"idem'],
    ids=["torn marker", "torn entry after marker"],
)
def test_a_thread_whose_ledger_was_torn_twice_still_approves_resumes_and_shows_history(
    workspace: Workspace, tail: str
) -> None:
    thread = "torn-2"
    run_scenario(workspace, CLEAN_BAD_DEPLOY, thread)
    ledger_path = workspace.audit_dir / f"{thread}.ledger.jsonl"
    ledger_path.write_text(tail, encoding="utf-8")

    approved = agentops(workspace, "approve", thread, "--decision", "approve")
    resumed = agentops(workspace, "resume", thread)
    history = agentops(workspace, "history", thread)

    assert approved.exit_code == 0, approved.output
    assert "status: resolved" in approved.stdout
    assert resumed.exit_code == 0, resumed.output
    assert history.exit_code == 0, history.output
    ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line).get("action") for line in ledger_lines[-1:]] == ["rollback_deploy"]


def test_a_ledger_entry_that_lost_only_its_newline_is_not_sealed_and_the_thread_resumes(
    workspace: Workspace,
) -> None:
    thread = "torn-3"
    run_scenario(workspace, CLEAN_BAD_DEPLOY, thread)
    first = agentops(workspace, "approve", thread, "--decision", "approve")
    ledger_path = workspace.audit_dir / f"{thread}.ledger.jsonl"
    ledger_path.write_bytes(ledger_path.read_bytes().rstrip(b"\r\n"))

    resumed = agentops(workspace, "resume", thread)
    history = agentops(workspace, "history", thread)

    assert first.exit_code == 0, first.output
    assert resumed.exit_code == 0, resumed.output
    assert history.exit_code == 0, history.output
    assert "torn" not in resumed.stderr
    assert "torn_seal" not in ledger_path.read_text(encoding="utf-8")
