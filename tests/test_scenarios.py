"""E16 to E18: every scenario file validates and references only its own signals (R10)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentops.sources.scenario import list_scenario_ids, load_scenario
from agentops.state import RcaOutput, Triage

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SCENARIO_FILES = sorted(SCENARIOS_DIR.glob("*.json"))


def signal_ids(raw: dict[str, Any]) -> list[str]:
    signals = raw["signals"]
    kinds = ("logs", "metrics", "deployments", "dependencies")
    return [item["id"] for kind in kinds for item in signals[kind]]


def write_variant(tmp_path: Path, raw: dict[str, Any], file_name: str) -> Path:
    path = tmp_path / file_name
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_both_increment_one_scenarios_are_listed() -> None:
    assert list_scenario_ids(SCENARIOS_DIR) == ["clean_bad_deploy", "deploy_red_herring_db"]


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda path: path.stem)
def test_scenario_files_validate_and_reference_only_their_own_signals(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))

    scenario = load_scenario(path)

    ids = signal_ids(raw)
    assert scenario.id == path.stem
    assert len(ids) == len(set(ids))
    assert set(scenario.required_evidence) <= set(ids)
    assert scenario.ground_truth.correct_action not in scenario.wrong_actions
    assert scenario.expected_outcome == "remediate"


@pytest.mark.parametrize(
    ("change", "offending_field"),
    [
        (lambda raw: raw.update(notes="remember to check the pool"), "notes"),
        (lambda raw: raw.pop("ground_truth"), "ground_truth"),
    ],
    ids=["unknown key", "missing key"],
)
def test_scenario_with_unknown_or_missing_key_is_rejected(
    tmp_path: Path, change: Any, offending_field: str
) -> None:
    raw = json.loads((SCENARIOS_DIR / "clean_bad_deploy.json").read_text(encoding="utf-8"))
    change(raw)
    path = write_variant(tmp_path, raw, "clean_bad_deploy.json")

    with pytest.raises(ValidationError) as raised:
        load_scenario(path)

    assert offending_field in str(raised.value)


def test_duplicate_signal_ids_and_unknown_required_evidence_are_rejected(tmp_path: Path) -> None:
    raw = json.loads((SCENARIOS_DIR / "clean_bad_deploy.json").read_text(encoding="utf-8"))
    raw["signals"]["deployments"][0]["id"] = raw["signals"]["logs"][0]["id"]
    raw["required_evidence"] = ["log-999"]
    path = write_variant(tmp_path, raw, "broken.json")

    with pytest.raises(ValidationError) as raised:
        load_scenario(path)

    message = str(raised.value)
    assert raw["signals"]["logs"][0]["id"] in message
    assert "log-999" in message


def test_correct_action_listed_as_wrong_is_rejected(tmp_path: Path) -> None:
    raw = json.loads((SCENARIOS_DIR / "clean_bad_deploy.json").read_text(encoding="utf-8"))
    raw["wrong_actions"].append(raw["ground_truth"]["correct_action"])
    path = write_variant(tmp_path, raw, "contradictory.json")

    with pytest.raises(ValidationError, match="wrong_actions"):
        load_scenario(path)


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda path: path.stem)
def test_scripted_outputs_are_valid_and_cite_real_signals(path: Path) -> None:
    scenario = load_scenario(path)

    triage = Triage.model_validate(scenario.scripted_outputs.triage)
    rca = RcaOutput.model_validate(scenario.scripted_outputs.rca)

    declared = set(scenario.signals.ids())
    cited = [
        *(cited for h in triage.hypotheses for cited in h.supporting_evidence),
        *(cited for h in triage.hypotheses for cited in h.contradicting_evidence),
        *rca.root_cause.evidence_ids,
        *rca.remediation.evidence_ids,
    ]
    assert rca.root_cause.label == scenario.ground_truth.root_cause
    assert rca.remediation.action == scenario.ground_truth.correct_action
    assert set(cited) <= declared
