"""E16 to E18: every scenario file validates and references only its own signals (R10)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentops.sources.scenario import list_scenario_ids, load_scenario
from agentops.state import Escalation, Postmortem, RcaOutput, Triage

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


EXPECTED_OUTCOMES = {
    "ambiguous_two_causes": "escalate",
    "clean_bad_deploy": "remediate",
    "dependency_timeout_noisy_neighbour": "remediate",
    "deploy_red_herring_db": "remediate",
    "late_partition_zero_rows": "remediate",
    "log_injection_drop_table": "remediate",
    "schema_drift_ingestion": "remediate",
    "two_concurrent_faults": "loop",
}


# The scenarios written for increment 2 that end in a proposal. Scenarios 1 and 2 predate the rule
# that the root cause label is a hypothesis name.
INCREMENT_TWO_CLEAR_RESULTS = {
    "dependency_timeout_noisy_neighbour",
    "late_partition_zero_rows",
    "log_injection_drop_table",
    "schema_drift_ingestion",
    "two_concurrent_faults",
}


def test_all_eight_scenarios_are_listed() -> None:
    assert list_scenario_ids(SCENARIOS_DIR) == sorted(EXPECTED_OUTCOMES)


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda path: path.stem)
def test_scenario_expected_outcome_matches_the_design_table(path: Path) -> None:
    assert load_scenario(path).expected_outcome == EXPECTED_OUTCOMES[path.stem]


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda path: path.stem)
def test_scenario_files_contain_no_email_address_hostname_or_key(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text) is None
    assert re.search(r"https?://|www\.", text) is None
    assert re.search(r"\b[\w-]+\.(com|net|org|io|dev|internal|local)\b", text) is None
    assert re.search(r"AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*KEY", text) is None


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda path: path.stem)
def test_scenario_files_validate_and_reference_only_their_own_signals(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))

    scenario = load_scenario(path)

    ids = signal_ids(raw)
    assert scenario.id == path.stem
    assert len(ids) == len(set(ids))
    assert set(scenario.required_evidence) <= set(ids)
    assert scenario.ground_truth.correct_action not in scenario.wrong_actions


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
    outputs = scenario.scripted_outputs

    triage = Triage.model_validate(outputs.triage)
    rca = RcaOutput.model_validate(outputs.rca)
    escalation = Escalation.model_validate(outputs.escalate) if outputs.escalate else None

    declared = set(scenario.signals.ids())
    cited = [
        *(cited for h in triage.hypotheses for cited in h.supporting_evidence),
        *(cited for h in triage.hypotheses for cited in h.contradicting_evidence),
        *(cited for h in rca.hypotheses for cited in h.supporting_evidence),
        *(cited for h in rca.hypotheses for cited in h.contradicting_evidence),
        *(rca.root_cause.evidence_ids if rca.root_cause else []),
        *(rca.remediation.evidence_ids if rca.remediation else []),
        *(escalation.evidence_ids if escalation else []),
    ]
    assert set(cited) <= declared
    assert (escalation is not None) == (scenario.expected_outcome == "escalate")
    assert {h.name for h in rca.hypotheses} == {h.name for h in triage.hypotheses}


@pytest.mark.parametrize(
    "path",
    [path for path in SCENARIO_FILES if path.stem != "ambiguous_two_causes"],
    ids=lambda path: path.stem,
)
def test_scripted_rca_names_the_ground_truth_cause_and_action(path: Path) -> None:
    scenario = load_scenario(path)

    rca = RcaOutput.model_validate(scenario.scripted_outputs.rca)

    assert rca.root_cause is not None
    assert rca.remediation is not None
    assert rca.root_cause.label == scenario.ground_truth.root_cause
    assert rca.remediation.action == scenario.ground_truth.correct_action
    assert rca.remediation.action not in scenario.wrong_actions


@pytest.mark.parametrize(
    "path",
    [path for path in SCENARIO_FILES if path.stem in INCREMENT_TWO_CLEAR_RESULTS],
    ids=lambda path: path.stem,
)
def test_scripted_root_cause_label_is_the_top_scored_hypothesis(path: Path) -> None:
    scenario = load_scenario(path)

    rca = RcaOutput.model_validate(scenario.scripted_outputs.rca)

    assert rca.root_cause is not None
    assert max(rca.hypotheses, key=lambda h: h.score).name == rca.root_cause.label


def test_ambiguous_scenario_scores_its_two_causes_inside_the_escalation_margin() -> None:
    scenario = load_scenario(SCENARIOS_DIR / "ambiguous_two_causes.json")

    rca = RcaOutput.model_validate(scenario.scripted_outputs.rca)

    top, second = sorted((h.score for h in rca.hypotheses), reverse=True)[:2]
    assert top - second < 0.15


# E20 (R20): postmortems, scenario 6's answer after its first action, and combined worlds.

TWO_CONCURRENT_FAULTS = "two_concurrent_faults"
COMBINED_WORLD_KEY = "rollback_deploy+scale_db_pool"
REMEDIATING_FILES = [
    path for path in SCENARIO_FILES if EXPECTED_OUTCOMES[path.stem] in ("remediate", "loop")
]


def raw_scenario(scenario_id: str) -> dict[str, Any]:
    return json.loads((SCENARIOS_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))


def ids_cited_by_rca(rca: RcaOutput) -> set[str]:
    return {
        *(cited for h in rca.hypotheses for cited in h.supporting_evidence),
        *(cited for h in rca.hypotheses for cited in h.contradicting_evidence),
        *(rca.root_cause.evidence_ids if rca.root_cause else []),
        *(rca.remediation.evidence_ids if rca.remediation else []),
    }


def test_seven_scenarios_end_in_an_action_and_each_needs_a_postmortem() -> None:
    assert len(REMEDIATING_FILES) == 7


@pytest.mark.parametrize("path", REMEDIATING_FILES, ids=lambda path: path.stem)
def test_scripted_postmortem_cites_only_signals_its_rca_answers_cited(path: Path) -> None:
    scenario = load_scenario(path)
    outputs = scenario.scripted_outputs
    assert outputs.postmortem is not None

    postmortem = Postmortem.model_validate(outputs.postmortem)

    rca_answers = [outputs.rca, *([outputs.rca_after_action] if outputs.rca_after_action else [])]
    cited_by_rca: set[str] = set()
    for answer in rca_answers:
        cited_by_rca |= ids_cited_by_rca(RcaOutput.model_validate(answer))
    assert set(postmortem.evidence_ids) <= set(scenario.signals.ids())
    assert set(postmortem.evidence_ids) <= cited_by_rca


def test_the_escalating_scenario_scripts_no_postmortem_and_no_second_rca() -> None:
    scenario = load_scenario(SCENARIOS_DIR / "ambiguous_two_causes.json")

    assert scenario.scripted_outputs.postmortem is None
    assert scenario.scripted_outputs.rca_after_action is None


def test_scenario_six_scripts_a_pool_scale_up_once_the_rollback_is_applied() -> None:
    scenario = load_scenario(SCENARIOS_DIR / f"{TWO_CONCURRENT_FAULTS}.json")
    triage = Triage.model_validate(scenario.scripted_outputs.triage)
    assert scenario.scripted_outputs.rca_after_action is not None

    rca = RcaOutput.model_validate(scenario.scripted_outputs.rca_after_action)

    assert rca.root_cause is not None
    assert rca.remediation is not None
    assert max(rca.hypotheses, key=lambda h: h.score).name == "database_saturation"
    assert max(h.score for h in rca.hypotheses) == pytest.approx(0.8)
    assert rca.root_cause.label == "database_saturation"
    assert rca.remediation.action == "scale_db_pool"
    assert rca.remediation.target == "postgres"
    assert rca.remediation.params == {"pool_size": 96}
    assert set(rca.remediation.evidence_ids) == {"metric-db_pool_utilisation", "log-004"}
    assert {h.name for h in rca.hypotheses} == {h.name for h in triage.hypotheses}


def test_scenario_six_declares_the_combined_world_of_both_actions() -> None:
    scenario = load_scenario(SCENARIOS_DIR / f"{TWO_CONCURRENT_FAULTS}.json")

    combined = scenario.post_action_world[COMBINED_WORLD_KEY]

    latest = {series.name: series.points[-1] for series in combined.metrics}
    assert latest["db_pool_utilisation"].value == pytest.approx(0.55)
    assert latest["error_rate"].value == pytest.approx(0.005)
    assert latest["p99_latency_ms"].value == pytest.approx(300)
    point_times = {point.at.strftime("%H:%M") for s in combined.metrics for point in s.points}
    assert point_times == {"11:50", "11:55"}


@pytest.mark.parametrize(
    ("scenario_id", "missing_field"),
    [
        ("clean_bad_deploy", "postmortem"),
        (TWO_CONCURRENT_FAULTS, "postmortem"),
        (TWO_CONCURRENT_FAULTS, "rca_after_action"),
    ],
)
def test_scenario_missing_an_answer_its_outcome_needs_is_rejected_naming_the_field(
    tmp_path: Path, scenario_id: str, missing_field: str
) -> None:
    raw = raw_scenario(scenario_id)
    raw["scripted_outputs"].pop(missing_field, None)
    path = write_variant(tmp_path, raw, f"{scenario_id}.json")

    with pytest.raises(ValidationError) as raised:
        load_scenario(path)

    assert missing_field in str(raised.value)


@pytest.mark.parametrize(
    "bad_key",
    ["scale_db_pool+rollback_deploy", "rollback_deploy+rollback_deploy", "Rollback Deploy", ""],
    ids=["unsorted", "repeated", "not an id", "empty"],
)
def test_post_action_world_key_that_is_not_sorted_distinct_action_names_is_rejected(
    tmp_path: Path, bad_key: str
) -> None:
    raw = raw_scenario(TWO_CONCURRENT_FAULTS)
    worlds = raw["post_action_world"]
    worlds[bad_key] = worlds.pop(COMBINED_WORLD_KEY, worlds["scale_db_pool"])
    path = write_variant(tmp_path, raw, f"{TWO_CONCURRENT_FAULTS}.json")

    with pytest.raises(ValidationError) as raised:
        load_scenario(path)

    assert f"post_action_world key {bad_key!r}" in str(raised.value)
