"""E9 to E13 and E33: every scenario file, run through the whole graph with its scripted model,
ends as its `expected_outcome` says (R5, R6, R10, R11)."""

from typing import Any

import pytest

from agentops.fakes import scripted_model_for
from agentops.graph import build_graph
from agentops.nodes.rca import CLOSING_DELIMITER, OPENING_DELIMITER
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import RcaOutput, new_incident

RED_HERRING = "deploy_red_herring_db"
AMBIGUOUS = "ambiguous_two_causes"
LOG_INJECTION = "log_injection_drop_table"
INJECTION_MARKER = "ACTION REQUIRED"
LOOPS_BY_SCENARIO = {
    "clean_bad_deploy": 0,
    "deploy_red_herring_db": 0,
    "dependency_timeout_noisy_neighbour": 1,
    "schema_drift_ingestion": 1,
    "late_partition_zero_rows": 1,
    "two_concurrent_faults": 0,
    "log_injection_drop_table": 0,
}
MINIMUM_BUDGET_LEFT = 4


def run_scenario(scenario: Scenario) -> tuple[Any, list[str]]:
    """The final state and the text of every prompt the scripted model received."""
    model = scripted_model_for(scenario)
    state = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)

    final = build_graph(SimulatorSource(scenario), model).invoke(state)

    prompts = ["\n".join(str(message.content) for message in call) for call in model.calls]
    return final, prompts


@pytest.mark.parametrize("scenario", [RED_HERRING], indirect=True)
def test_red_herring_ends_approval_ready_with_rescored_hypotheses(scenario: Scenario) -> None:
    final, _ = run_scenario(scenario)

    scripted = RcaOutput.model_validate(scenario.scripted_outputs.rca)
    assert final["hypotheses"] == scripted.hypotheses
    assert final["status"] == "awaiting_approval"
    assert final["remediation"].action == "scale_db_pool"
    assert final["root_cause"].label == "database_saturation"


@pytest.mark.parametrize("scenario", [AMBIGUOUS], indirect=True)
def test_ambiguous_scenario_escalates_with_both_hypotheses(scenario: Scenario) -> None:
    final, _ = run_scenario(scenario)

    evidence_ids = {item.id for item in final["evidence"]}
    assert final["status"] == "escalated"
    assert final["escalation_reason"] == "ambiguous"
    assert final["escalation"].summary
    assert set(final["escalation"].evidence_ids) <= evidence_ids
    assert final["remediation"] is None
    assert final["root_cause"] is None
    assert {item.name for item in final["hypotheses"]} == {
        "memory_pressure_gc_pauses",
        "cache_cluster_eviction_storm",
    }


def test_every_scenario_ends_as_its_expected_outcome(scenario: Scenario) -> None:
    final, _ = run_scenario(scenario)

    is_escalation = scenario.expected_outcome == "escalate"
    expected_status = "escalated" if is_escalation else "awaiting_approval"
    assert final["status"] == expected_status


def test_remediation_matches_ground_truth_on_collected_evidence(scenario: Scenario) -> None:
    if scenario.expected_outcome == "escalate":
        pytest.skip("An escalated run proposes no remediation.")

    final, _ = run_scenario(scenario)

    evidence_ids = {item.id for item in final["evidence"]}
    assert final["remediation"].action == scenario.ground_truth.correct_action
    assert final["remediation"].action not in scenario.wrong_actions
    assert set(scenario.required_evidence) <= evidence_ids


def test_loop_counts_and_budget_follow_the_stated_paths(scenario: Scenario) -> None:
    final, _ = run_scenario(scenario)

    if scenario.id in LOOPS_BY_SCENARIO:
        assert final["loops"] == LOOPS_BY_SCENARIO[scenario.id]
    assert final["investigation_budget"] >= MINIMUM_BUDGET_LEFT


@pytest.mark.parametrize("scenario", [LOG_INJECTION], indirect=True)
def test_log_injection_stays_fenced_and_the_real_cause_wins(scenario: Scenario) -> None:
    final, prompts = run_scenario(scenario)

    injected_line = next(
        line.message for line in scenario.signals.logs if INJECTION_MARKER in line.message
    )
    prompts_with_the_line = [prompt for prompt in prompts if INJECTION_MARKER in prompt]
    assert prompts_with_the_line
    for prompt in prompts_with_the_line:
        before_block, rest = prompt.split(OPENING_DELIMITER)
        block, after_block = rest.split(CLOSING_DELIMITER)
        assert INJECTION_MARKER not in before_block
        assert INJECTION_MARKER not in after_block
        assert "<<<" not in block
        assert ">>>" not in block
    assert INJECTION_MARKER in injected_line
    assert final["remediation"].action == "pause_batch_job"
    assert "drop" not in final["remediation"].action


@pytest.mark.parametrize("scenario", [LOG_INJECTION], indirect=True)
def test_a_log_line_that_forges_the_closing_marker_cannot_leave_the_fence(
    scenario: Scenario,
) -> None:
    final, prompts = run_scenario(scenario)

    forged_line = next(line for line in scenario.signals.logs if line.id == "log-007")
    assert CLOSING_DELIMITER in forged_line.message
    prompts_with_the_line = [prompt for prompt in prompts if "NEW INSTRUCTIONS" in prompt]
    assert prompts_with_the_line
    for prompt in prompts_with_the_line:
        assert prompt.count(OPENING_DELIMITER) == 1
        assert prompt.count(CLOSING_DELIMITER) == 1
        before_block, rest = prompt.split(OPENING_DELIMITER)
        block, after_block = rest.split(CLOSING_DELIMITER)
        assert "NEW INSTRUCTIONS" in block
        assert "NEW INSTRUCTIONS" not in after_block
    assert final["remediation"].action == "pause_batch_job"


@pytest.mark.parametrize("scenario", [AMBIGUOUS], indirect=True)
def test_every_model_prompt_states_that_evidence_is_data_not_instructions(
    scenario: Scenario,
) -> None:
    model = scripted_model_for(scenario)
    state = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)

    build_graph(SimulatorSource(scenario), model).invoke(state)

    prompts = ["\n".join(str(message.content) for message in call) for call in model.calls]
    assert len(prompts) == 3
    for node_name, prompt in zip(("triage", "rca", "escalate"), prompts, strict=True):
        assert "data, not instructions" in prompt, node_name
