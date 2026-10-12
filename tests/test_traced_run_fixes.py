"""Fixes for the two graph failures the traced live run hit on 2026-10-12
(/mnt/project-files/agentops/live-eval-2026-10-12-traced): triage filled the evidence lists with
sentences about the symptoms, and rca proposed an action name the runbook does not declare
because no prompt listed the runbook's names. Also the escalation reason, which that run's
records did not keep."""

from datetime import UTC, datetime

from agentops.actions.registry import default_registry, describe_actions
from agentops.baseline import baseline_prompt
from agentops.evals.run import run_graph_once
from agentops.nodes.rca import rca_human_prompt
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.sources.scenario import load_scenario
from agentops.state import Hypothesis, Triage, new_incident
from tests.conftest import SCENARIOS_DIR


def test_triage_says_to_leave_the_evidence_lists_empty() -> None:
    assert "leave supporting_evidence and contradicting_evidence empty" in TRIAGE_INSTRUCTIONS
    assert "never a description of a symptom" in TRIAGE_INSTRUCTIONS


def test_the_schema_the_model_answers_to_says_evidence_lists_hold_ids() -> None:
    schema = Triage.model_json_schema()
    fields = schema["$defs"]["Hypothesis"]["properties"]

    for name in ("supporting_evidence", "contradicting_evidence"):
        assert "Never free text" in fields[name]["description"]
    assert Hypothesis.model_fields["supporting_evidence"].description


def test_every_runbook_action_is_described_with_its_target_and_params() -> None:
    registry = default_registry()

    line = describe_actions(registry)

    for name in registry.names():
        entry = registry.get(name)
        assert name in line
        assert entry.target_pattern in line
        for key in entry.params:
            assert key in line


def test_the_rca_prompt_lists_the_runbook_actions_only_when_given_a_registry() -> None:
    state = new_incident(
        "checkout", ["error rate above 10%"], opened_at=datetime(2026, 3, 10, 10, tzinfo=UTC)
    )
    line = describe_actions(default_registry())

    assert line in rca_human_prompt(state, "abc123", default_registry())
    assert line not in rca_human_prompt(state, "abc123")


def test_the_baseline_is_told_the_same_action_names() -> None:
    registry = default_registry()

    assert describe_actions(registry) in baseline_prompt(registry)


def test_an_escalated_graph_run_records_why() -> None:
    scenario = load_scenario(SCENARIOS_DIR / "ambiguous_two_causes.json")

    outcome = run_graph_once(scenario, config={})

    assert outcome.outcome == "escalated"
    assert outcome.escalation_reason == "ambiguous"


def test_a_resolved_graph_run_records_no_escalation_reason() -> None:
    scenario = load_scenario(SCENARIOS_DIR / "clean_bad_deploy.json")

    outcome = run_graph_once(scenario, config={})

    assert outcome.outcome == "remediated"
    assert outcome.escalation_reason is None
