"""E1, E27 to E29, E35, E36: the `agentops` command line, driven through Typer's runner."""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import agentops
from agentops import cli
from agentops.cli import app
from agentops.sources.scenario import load_scenario

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
