"""The eval runner's capture of evidence at the first proposal (`evidence_ids_at_proposal`).

A run that never decided, or that is still paused when the approval limit is reached, has no
honest evidence list; it must become a failed row, not a quietly lenient one.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import agentops.evals.run as eval_run
from agentops.evals.outcome import evidence_ids_of
from agentops.evals.run import evidence_ids_at_proposal, measured_run, run_graph_once
from agentops.sources.scenario import load_scenario
from agentops.state import IncidentState, new_incident

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
TWO_FAULTS = "two_concurrent_faults"
CLEAN = "clean_bad_deploy"
# What the second round adds on two_concurrent_faults: the action evidence and the verify reads.
SECOND_ROUND_PREFIXES = ("action-", "verify-")


def scenario_named(scenario_id: str):
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


@dataclass
class StubSnapshot:
    interrupts: tuple[Any, ...] = ()


@dataclass
class StubGraph:
    """A graph that streams the given chunks on every pass and stays paused `pauses` times."""

    chunks: list[dict[str, Any]]
    pauses: int
    passes: int = field(default=0)

    def stream(self, value: Any, config: Any, stream_mode: str):
        self.passes += 1
        yield from self.chunks

    def get_state(self, config: Any) -> StubSnapshot:
        return StubSnapshot(interrupts=("paused",) if self.passes <= self.pauses else ())


def blank_incident() -> IncidentState:
    return new_incident(
        "checkout-api",
        ["errors"],
        opened_at=datetime(2026, 3, 10, 10, 30, tzinfo=UTC),
        scenario_id=CLEAN,
    )


def deciding_chunk() -> dict[str, Any]:
    incident = blank_incident()
    return {**incident, "escalation_reason": "needs a human"}


def test_the_captured_ids_exclude_the_second_round_on_a_looping_scenario() -> None:
    captured: dict[str, Any] = {}
    original = eval_run.outcome_from_state

    def recording(state: IncidentState, ids: list[str], *args: Any, **kwargs: Any):
        captured["final"] = evidence_ids_of(state)
        captured["at_proposal"] = ids
        return original(state, ids, *args, **kwargs)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(eval_run, "outcome_from_state", recording)
        outcome = run_graph_once(scenario_named(TWO_FAULTS), config={})

    at_proposal = set(captured["at_proposal"])
    second_round = {item for item in captured["final"] if item.startswith(SECOND_ROUND_PREFIXES)}
    assert outcome.evidence_ids_before_proposal == captured["at_proposal"]
    assert second_round
    assert not at_proposal & second_round
    assert at_proposal < set(captured["final"])
    assert {"deploy-001", "log-003", "metric-db_pool_utilisation"} <= at_proposal


def test_a_run_still_paused_after_the_approval_limit_is_a_failed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_run, "MAX_APPROVALS", 1)

    outcome = measured_run("graph", scenario_named(TWO_FAULTS), {})

    assert outcome.outcome == "failed"
    assert outcome.failure == "RuntimeError"
    assert outcome.applies == []


def test_the_same_scenario_within_the_approval_limit_is_not_a_failed_row() -> None:
    outcome = measured_run("graph", scenario_named(TWO_FAULTS), {})

    assert outcome.outcome == "looped_then_remediated"


def test_a_run_that_never_proposed_or_escalated_raises() -> None:
    undecided = blank_incident()
    graph = StubGraph(chunks=[dict(undecided)], pauses=0)

    with pytest.raises(RuntimeError, match="without proposing"):
        evidence_ids_at_proposal(graph, undecided, {})  # type: ignore[arg-type]


def test_a_run_still_paused_when_the_limit_is_reached_raises() -> None:
    incident = blank_incident()
    graph = StubGraph(chunks=[deciding_chunk()], pauses=100)

    with pytest.raises(RuntimeError, match="waiting for an approval"):
        evidence_ids_at_proposal(graph, incident, {})  # type: ignore[arg-type]

    assert graph.passes == eval_run.MAX_APPROVALS + 1


def test_a_run_that_decides_and_finishes_returns_its_ids_even_when_there_are_none() -> None:
    incident = blank_incident()
    graph = StubGraph(chunks=[deciding_chunk()], pauses=0)

    assert evidence_ids_at_proposal(graph, incident, {}) == []  # type: ignore[arg-type]
