"""Routing is a pure function of the state: scores, order, exclusions and the budget prefix."""

from datetime import UTC, datetime

import pytest
from langgraph.types import Send

from agentops.nodes.router import (
    deciding_sources,
    route_to_sources,
    score_sources,
    sources_to_ask,
)
from agentops.state import (
    INITIAL_INVESTIGATION_BUDGET,
    Action,
    Evidence,
    Hypothesis,
    IncidentState,
    new_incident,
)

OPENED_AT = datetime(2026, 3, 10, 10, 30, tzinfo=UTC)


def incident_with(
    hypothesis_names: list[str],
    *,
    evidence_tools: tuple[str, ...] = (),
    action_tools: tuple[str, ...] = (),
) -> IncidentState:
    """A state holding open hypotheses, one evidence item per tool and one ok action per tool."""
    state = new_incident("orders", ["slow"], opened_at=OPENED_AT)
    state["hypotheses"] = [Hypothesis(name=name, score=0.5) for name in hypothesis_names]
    state["evidence"] = [
        Evidence(
            id=f"{tool}-{index}",
            source=tool,
            kind="log",
            payload={"id": f"{tool}-{index}"},
            collected_at=OPENED_AT,
        )
        for index, tool in enumerate(evidence_tools)
    ]
    state["actions"] = [Action(tool=tool, ok=True, latency_ms=1.0) for tool in action_tools]
    return state


def sent_nodes(routed: list[Send] | str) -> list[str]:
    assert isinstance(routed, list)
    return [send.node for send in routed]


def test_router_scores_sources_against_open_hypotheses_in_fixed_order() -> None:
    state = new_incident("orders", ["slow"], opened_at=OPENED_AT)
    state["hypotheses"] = [
        Hypothesis(name="recent_deploy_regression", score=0.7),
        Hypothesis(name="database_saturation", score=0.2),
    ]

    scores = score_sources(state["hypotheses"])

    assert scores == {"logs": 2, "metrics": 1, "deploys": 1}
    assert sent_nodes(route_to_sources(state)) == ["logs", "metrics", "deploys"]


def test_router_is_deterministic_and_skips_answered_or_exhausted_sources() -> None:
    state = incident_with(
        ["recent_deploy_regression", "database_saturation"],
        evidence_tools=("get_metrics",),
        action_tools=("get_metrics", "get_dependency_health", "get_deployments", "get_deployments"),
    )

    first = route_to_sources(state)
    second = route_to_sources(state)

    assert sent_nodes(first) == ["logs"]
    assert sent_nodes(first) == sent_nodes(second)


def test_a_failed_first_attempt_does_not_count_as_a_round() -> None:
    state = incident_with(["recent_deploy_regression"])
    state["actions"] = [Action(tool="get_deployments", ok=False, latency_ms=1.0, attempt=1)]

    assert "deploys" in sources_to_ask(state)


def test_a_source_that_failed_its_last_attempt_has_used_up_a_round() -> None:
    state = incident_with(["recent_deploy_regression"])
    failed_round = [
        Action(tool="get_deployments", ok=False, latency_ms=1.0, attempt=attempt)
        for attempt in (1, 2)
    ]
    state["actions"] = failed_round + failed_round

    assert "deploys" not in sources_to_ask(state)


def test_hypothesis_matching_no_keyword_counts_for_every_source() -> None:
    hypotheses = [Hypothesis(name="something_odd", score=0.5)]

    assert score_sources(hypotheses) == {"logs": 1, "metrics": 1, "deploys": 1}


def test_closed_hypotheses_do_not_score() -> None:
    hypotheses = [Hypothesis(name="database_saturation", score=0.19)]

    assert score_sources(hypotheses) == {"logs": 0, "metrics": 0, "deploys": 0}


def test_sources_are_ordered_by_score_then_by_the_fixed_order() -> None:
    state = incident_with(["recent_deploy_regression", "release_rollout_gap"])

    assert sources_to_ask(state) == ["logs", "deploys"]


def test_router_keeps_only_the_longest_prefix_that_fits_the_budget() -> None:
    """Each source reserves its nominal cost for both read attempts (metrics 4, deploys 2), so a
    round cannot overspend even if every read fails and retries (spec R3, ADR 0004)."""

    def route_with(reads_left: int) -> list[Send] | str:
        spent_reads = ("get_logs",) * (INITIAL_INVESTIGATION_BUDGET - reads_left)
        state = incident_with(
            ["database_saturation", "recent_deploy_regression"],
            evidence_tools=("get_logs",),
            action_tools=spent_reads,
        )
        return route_to_sources(state)

    assert route_with(1) == "merge"
    assert route_with(3) == "merge"
    assert sent_nodes(route_with(4)) == ["metrics"]
    assert sent_nodes(route_with(5)) == ["metrics"]
    assert sent_nodes(route_with(6)) == ["metrics", "deploys"]


def test_deciding_sources_are_the_specific_ones_or_all_three() -> None:
    assert deciding_sources("recent_deploy_regression") == ["deploys"]
    assert deciding_sources("database_saturation") == ["metrics"]
    assert deciding_sources("pool_exhaustion_after_redeploy") == ["metrics", "deploys"]
    assert deciding_sources("something_odd") == ["logs", "metrics", "deploys"]


@pytest.mark.parametrize(
    "name", ["conversion_rate_drop", "exception_thrown_in_handler", "feature_flag_flip"]
)
def test_a_keyword_inside_a_longer_word_does_not_match(name: str) -> None:
    """'version' in conversion, 'row' in thrown and 'lag' in flag are not what the name is about."""
    scores = score_sources([Hypothesis(name=name, score=0.5)])

    assert scores == {"logs": 1, "metrics": 1, "deploys": 1}


@pytest.mark.parametrize(
    "name", ["misconfigured_connection_pool", "pool_exhaustion_after_redeploy"]
)
def test_a_keyword_after_a_mis_re_or_un_prefix_still_matches(name: str) -> None:
    """'misconfigured' is about config and 'redeploy' about deploys, so deploys must be asked."""
    scores = score_sources([Hypothesis(name=name, score=0.5)])

    assert scores == {"logs": 1, "metrics": 1, "deploys": 1}


def test_a_prefixed_keyword_routes_to_the_source_that_judges_it() -> None:
    scores = score_sources([Hypothesis(name="misconfigured_cache", score=0.5)])

    assert scores == {"logs": 1, "metrics": 0, "deploys": 1}


def test_a_keyword_matches_a_name_token_that_starts_with_it() -> None:
    scores = score_sources(
        [
            Hypothesis(name="memory_pressure", score=0.5),
            Hypothesis(name="deployment_regression", score=0.5),
        ]
    )

    assert scores == {"logs": 2, "metrics": 1, "deploys": 1}
