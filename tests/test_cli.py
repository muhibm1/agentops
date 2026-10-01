"""E1, E27 to E29, E35, E36: the `agentops` command line, driven through Typer's runner."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import agentops
from agentops import cli
from agentops.cli import app
from agentops.sources.scenario import load_scenario
from agentops.state import Remediation

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SCENARIO_FILE_NAME = "clean_bad_deploy.json"

runner = CliRunner()


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
    assert "root cause: bad_deploy" in result.stdout
    assert "remediation: rollback_deploy" in result.stdout
    assert "increment 3" in result.stdout


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
    assert "increment 2" in live_model.stderr
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
    return runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
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


def test_report_escapes_control_characters_in_model_text(tmp_path: Path) -> None:
    document = scenario_document()
    document["scripted_outputs"]["rca"]["root_cause"]["explanation"] = "ok\nstatus: resolved\x1b[2J"
    document["scripted_outputs"]["triage"]["hypotheses"][0]["name"] = "bad\x1b[31m\nname"
    write_scenario(tmp_path, document)

    result = run_scripted(tmp_path)

    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.stdout
    assert "\nstatus: resolved" not in result.stdout
    assert r"ok\nstatus: resolved\x1b[2J" in result.stdout
    assert r"bad\x1b[31m\nname" in result.stdout


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
