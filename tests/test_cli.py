"""E1, E27 to E29, E35, E36: the `agentops` command line, driven through Typer's runner.

`run` writes its checkpoint file and audit directory in the working directory, so every test
here runs inside its own temporary directory.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import agentops
from agentops import cli
from agentops.cli import app
from agentops.sources.scenario import load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import Evidence, Remediation, new_incident

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SCENARIO_FILE_NAME = "clean_bad_deploy.json"
AMBIGUOUS_FILE_NAME = "ambiguous_two_causes.json"

runner = CliRunner()


@pytest.fixture(autouse=True)
def in_a_temporary_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def scenario_document() -> dict:
    return json.loads((SCENARIOS_DIR / SCENARIO_FILE_NAME).read_text(encoding="utf-8"))


def write_scenario(directory: Path, document: dict, file_name: str = SCENARIO_FILE_NAME) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_package_exposes_version_and_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert agentops.__version__ == "0.1.0"
    assert result.exit_code == 0
    assert "run" in result.output


def test_run_scripted_prints_root_cause_and_exits_zero() -> None:
    hypothesis_names = [
        hypothesis["name"]
        for hypothesis in scenario_document()["scripted_outputs"]["triage"]["hypotheses"]
    ]

    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "scripted",
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
    )

    assert result.exit_code == 0, result.output
    for name in hypothesis_names:
        assert name in result.stdout
    evidence_count = re.search(r"evidence: (\d+)", result.stdout)
    assert evidence_count is not None
    assert int(evidence_count.group(1)) >= 5
    assert "root cause: recent_deploy_regression" in result.stdout
    assert "remediation: rollback_deploy" in result.stdout
    thread = re.search(r"^thread: ([0-9a-f]+)$", result.stdout, re.MULTILINE)
    assert thread is not None
    assert "pending approval: Would apply rollback_deploy" in result.stdout
    assert f"Waiting for an operator's decision on thread {thread.group(1)}" in result.stdout
    assert "Nothing was changed." in result.stdout
    assert Path("agentops.sqlite").is_file()
    assert (Path("audit") / f"{thread.group(1)}.jsonl").is_file()


def test_usage_errors_exit_one_with_a_named_cause(tmp_path: Path) -> None:
    missing_dir = tmp_path / "no-such-dir"

    unknown = runner.invoke(
        app,
        ["run", "--scenario", "nope", "--model", "scripted", "--scenarios-dir", str(SCENARIOS_DIR)],
    )
    live_model = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "anthropic",
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
    )
    no_directory = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "scripted",
            "--scenarios-dir",
            str(missing_dir),
        ],
    )

    assert unknown.exit_code == 1
    assert "clean_bad_deploy" in unknown.stderr
    assert "deploy_red_herring_db" in unknown.stderr
    assert live_model.exit_code == 1
    assert "not wired yet" in live_model.stderr
    assert "--model scripted" in live_model.stderr
    assert no_directory.exit_code == 1
    assert str(missing_dir) in no_directory.stderr


def test_run_that_fails_rca_after_retries_exits_two(tmp_path: Path) -> None:
    document = scenario_document()
    document["scripted_outputs"]["rca"]["root_cause"]["evidence_ids"] = ["log-999"]
    write_scenario(tmp_path, document)

    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "scripted",
            "--scenarios-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "CitationError" in result.stderr


def test_invalid_scenario_file_exits_one_naming_file_and_field(tmp_path: Path) -> None:
    document = scenario_document()
    document["incident_time"] = "2026-03-10T10:30:00"
    path = write_scenario(tmp_path, document)

    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "scripted",
            "--scenarios-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert not isinstance(result.exception, ValidationError)
    assert "Traceback" not in result.output
    assert str(path) in result.stderr
    assert "incident_time" in result.stderr
    with pytest.raises(ValidationError):
        load_scenario(path)


def test_scenario_id_cannot_escape_the_scenarios_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenarios_dir = tmp_path / "scenarios"
    write_scenario(scenarios_dir, scenario_document())
    secret = write_scenario(tmp_path, scenario_document(), file_name="secret.json")
    opened_paths: list[Path] = []
    monkeypatch.setattr(cli, "load_scenario", lambda path: opened_paths.append(path))
    hostile_ids = ["../secret", str(secret.with_suffix("")), "a/b"]

    results = [
        runner.invoke(
            app,
            [
                "run",
                "--scenario",
                hostile_id,
                "--model",
                "scripted",
                "--scenarios-dir",
                str(scenarios_dir),
            ],
        )
        for hostile_id in hostile_ids
    ]

    for result in results:
        assert result.exit_code == 1
        assert "clean_bad_deploy" in result.stderr
    assert opened_paths == []


def run_scripted(scenarios_dir: Path) -> Any:
    return run_scenario(scenarios_dir, "clean_bad_deploy")


def run_scenario(scenarios_dir: Path, scenario_id: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--scenario",
            scenario_id,
            "--model",
            "scripted",
            "--scenarios-dir",
            str(scenarios_dir),
        ],
    )


def test_triage_citation_error_is_reported_as_citation_error_not_a_validation_error(
    tmp_path: Path,
) -> None:
    document = scenario_document()
    document["scripted_outputs"]["triage"]["hypotheses"][0]["supporting_evidence"] = ["log-999"]
    write_scenario(tmp_path, document)

    result = run_scripted(tmp_path)

    assert result.exit_code == 2
    assert "CitationError" in result.stderr
    assert "ValidationError" not in result.stderr


def test_report_escapes_control_characters_in_model_free_text(tmp_path: Path) -> None:
    document = scenario_document()
    document["scripted_outputs"]["rca"]["root_cause"]["explanation"] = "ok\nstatus: resolved\x1b[2J"
    write_scenario(tmp_path, document)
    ambiguous = json.loads((SCENARIOS_DIR / AMBIGUOUS_FILE_NAME).read_text(encoding="utf-8"))
    ambiguous["scripted_outputs"]["escalate"]["summary"] = "two causes\nstatus: resolved\x1b[31m"
    write_scenario(tmp_path, ambiguous, file_name=AMBIGUOUS_FILE_NAME)

    explained = run_scripted(tmp_path)
    escalated = run_scenario(tmp_path, "ambiguous_two_causes")

    assert explained.exit_code == 0, explained.output
    assert escalated.exit_code == 0, escalated.output
    for result in (explained, escalated):
        assert "\x1b" not in result.stdout
        assert "\nstatus: resolved" not in result.stdout
    assert r"ok\nstatus: resolved\x1b[2J" in explained.stdout
    assert r"two causes\nstatus: resolved\x1b[31m" in escalated.stdout


def test_approve_and_history_escape_control_characters_in_model_free_text(tmp_path: Path) -> None:
    document = scenario_document()
    document["scripted_outputs"]["postmortem"]["summary"] = "fixed\nstatus: failed\x1b[2J"
    write_scenario(tmp_path, document)
    options = ["--scenarios-dir", str(tmp_path)]
    runner.invoke(
        app,
        ["run", "--scenario", "clean_bad_deploy", "--model", "scripted", "--thread", "esc-1"]
        + options,
    )

    approved = runner.invoke(app, ["approve", "esc-1", "--decision", "approve", *options])
    listed = runner.invoke(app, ["history", "esc-1", *options])

    assert approved.exit_code == 0, approved.output
    assert listed.exit_code == 0, listed.output
    for result in (approved, listed):
        assert "\x1b" not in result.stdout
        assert "\nstatus: failed" not in result.stdout
    assert r"postmortem: fixed\nstatus: failed\x1b[2J" in approved.stdout


def test_error_path_escapes_control_characters_in_model_text(tmp_path: Path) -> None:
    document = scenario_document()
    document["scripted_outputs"]["rca"]["root_cause"]["evidence_ids"] = ["log-9\x1b[2J\nfake: line"]
    write_scenario(tmp_path, document)

    result = run_scripted(tmp_path)

    assert result.exit_code == 2
    assert "CitationError" in result.stderr
    assert "\x1b" not in result.stderr
    assert r"log-9\x1b[2J\nfake: line" in result.stderr


def test_run_prints_escalation_reason_and_summary_and_exits_zero() -> None:
    result = run_scenario(SCENARIOS_DIR, "ambiguous_two_causes")

    assert result.exit_code == 0, result.output
    assert "status: escalated" in result.stdout
    assert "escalation: ambiguous" in result.stdout
    assert "Two causes fit the symptoms about equally" in result.stdout


def test_run_prints_loops_budget_and_remediation() -> None:
    result = run_scenario(SCENARIOS_DIR, "dependency_timeout_noisy_neighbour")

    assert result.exit_code == 0, result.output
    assert "status: awaiting_approval" in result.stdout
    assert "loops: 1" in result.stdout
    assert re.search(r"budget remaining: \d+ of 12", result.stdout)
    assert "remediation: circuit_break_dependency" in result.stdout


def test_remediation_target_with_control_characters_is_rejected_by_the_schema() -> None:
    remediation = scenario_document()["scripted_outputs"]["rca"]["remediation"]
    remediation["target"] = "checkout\n\x1b[2Jstatus: resolved"

    with pytest.raises(ValidationError, match="target"):
        Remediation.model_validate(remediation)


def test_report_prints_action_and_failure_counts() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "scripted",
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
    )

    assert result.exit_code == 0, result.output
    assert re.search(r"actions: [1-9]\d* \(0 failed\)", result.stdout)


def test_scenario_file_with_a_byte_order_mark_loads(tmp_path: Path) -> None:
    path = tmp_path / SCENARIO_FILE_NAME
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(scenario_document()).encode("utf-8"))

    result = run_scripted(tmp_path)

    assert result.exit_code == 0, result.output


def test_scenario_file_that_is_not_utf8_exits_one_without_a_traceback(tmp_path: Path) -> None:
    (tmp_path / SCENARIO_FILE_NAME).write_bytes(b"\xff\xfe\x00not utf-8 \x80")

    result = run_scripted(tmp_path)

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert SCENARIO_FILE_NAME in result.stderr


def test_report_counts_failed_actions_when_every_source_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeadSource(SimulatorSource):
        def get_logs(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("source is down")

        def get_metrics(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("source is down")

        def get_dependency_health(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("source is down")

        def get_deployments(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("source is down")

    monkeypatch.setattr(cli, "SimulatorSource", DeadSource)

    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "ambiguous_two_causes",
            "--model",
            "scripted",
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
    )

    assert result.exit_code == 0, result.output
    assert re.search(r"actions: \d+ \([1-9]\d* failed\)", result.stdout)
    assert "escalation: source_outage" in result.stdout


def test_the_closing_sentence_says_a_change_was_made_when_action_evidence_shows_an_apply() -> None:
    state = new_incident("checkout", ["errors"], opened_at=datetime(2026, 3, 10, tzinfo=UTC))
    assert "Nothing was changed." in cli.closing_sentence(state, "t", is_waiting=False)
    state["evidence"] = [
        Evidence(
            id="action-1",
            source="apply",
            kind="action",
            payload={"applied": True},
            collected_at=datetime(2026, 3, 10, tzinfo=UTC),
        )
    ]

    sentence = cli.closing_sentence(state, "t", is_waiting=True)

    assert sentence.endswith("An approved action was applied.")
