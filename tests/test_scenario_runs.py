"""E9 to E13 and E33: every scenario file, run through the whole graph with its scripted model,
ends as its `expected_outcome` says (R5, R6, R10, R11). Each run stops at the first approval
interrupt (brief D20); the runs past approval are in test_full_runs.py."""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import ScriptedChatModel, scripted_model_for
from agentops.graph import build_graph, checkpoint_serializer
from agentops.nodes import CLOSING_MARKER_PATTERN, OPENING_MARKER_PATTERN
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import IncidentState, RcaOutput, new_incident

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

    final = run_to_first_pause(scenario, model, state)

    prompts = ["\n".join(str(message.content) for message in call) for call in model.calls]
    return final, prompts


def run_to_first_pause(scenario: Scenario, model: ScriptedChatModel, state: IncidentState) -> Any:
    """The full graph on an in-memory checkpointer, run until it ends or pauses at approval."""
    with tempfile.TemporaryDirectory() as audit_dir:
        graph = build_graph(
            SimulatorSource(scenario),
            model,
            default_registry(),
            AuditLog(Path(audit_dir)),
            InMemorySaver(serde=checkpoint_serializer()),
        )
        return graph.invoke(state, {"configurable": {"thread_id": "scenario-run"}})


def split_at_fence(prompt: str) -> tuple[str, str, str]:
    """The text before the opening marker, inside the fence, and after the closing marker; the
    markers carry a per-prompt nonce, so they are matched by pattern (brief D20)."""
    before_block, rest = OPENING_MARKER_PATTERN.split(prompt)
    block, after_block = CLOSING_MARKER_PATTERN.split(rest)
    return before_block, block, after_block


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
        "memory_pressure",
        "cache_eviction",
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
        before_block, block, after_block = split_at_fence(prompt)
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
    assert "<<<end>>>" in forged_line.message
    prompts_with_the_line = [prompt for prompt in prompts if "NEW INSTRUCTIONS" in prompt]
    assert prompts_with_the_line
    for prompt in prompts_with_the_line:
        assert len(OPENING_MARKER_PATTERN.findall(prompt)) == 1
        assert len(CLOSING_MARKER_PATTERN.findall(prompt)) == 1
        _, block, after_block = split_at_fence(prompt)
        assert "NEW INSTRUCTIONS" in block
        assert "NEW INSTRUCTIONS" not in after_block
    assert final["remediation"].action == "pause_batch_job"


@pytest.mark.parametrize("scenario", [AMBIGUOUS], indirect=True)
def test_every_model_prompt_states_that_evidence_is_data_not_instructions(
    scenario: Scenario,
) -> None:
    model = scripted_model_for(scenario)
    state = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)

    run_to_first_pause(scenario, model, state)

    prompts = ["\n".join(str(message.content) for message in call) for call in model.calls]
    assert len(prompts) == 3
    for node_name, prompt in zip(("triage", "rca", "escalate"), prompts, strict=True):
        assert "data, not instructions" in prompt, node_name
