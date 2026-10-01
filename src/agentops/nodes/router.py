"""The router: which evidence sources to ask next, decided by code from the state alone (ADR 0003).

A source scores one point for each open hypothesis (score at or above 0.2) it can confirm or rule
out, judged by keywords in the hypothesis name. Sources are ordered by score, then logs, metrics,
deploys. A source that already holds evidence or was asked in two rounds is dropped. The longest
prefix whose nominal cost fits the remaining budget is kept.
"""

from langgraph.types import Send

from agentops.nodes.evidence import (
    READ_ATTEMPTS,
    SOURCE_READ_TOOLS,
    SourceName,
    source_read_attempts,
)
from agentops.state import INITIAL_INVESTIGATION_BUDGET, Hypothesis, IncidentState

OPEN_HYPOTHESIS_MIN_SCORE = 0.2
MAX_ROUNDS_PER_SOURCE = 2
SOURCE_ORDER: tuple[SourceName, ...] = ("logs", "metrics", "deploys")
NOMINAL_COST: dict[SourceName, int] = {"logs": 1, "metrics": 2, "deploys": 1}
METRIC_KEYWORDS = (
    "saturation",
    "pool",
    "latency",
    "cpu",
    "memory",
    "capacity",
    "throughput",
    "timeout",
    "dependency",
    "upstream",
    "downstream",
    "neighbour",
    "neighbor",
    "lag",
    "volume",
    "row",
    "partition",
)
DEPLOY_KEYWORDS = (
    "deploy",
    "release",
    "version",
    "rollout",
    "regression",
    "config",
    "migration",
    "schema",
)


def route_to_sources(state: IncidentState) -> list[Send] | str:
    """The conditional edge after triage: one `Send` per source to ask, or `merge` if none."""
    sources = affordable(state, sources_to_ask(state))
    if not sources:
        return "merge"
    return [Send(source, state) for source in sources]


def score_sources(hypotheses: list[Hypothesis]) -> dict[SourceName, int]:
    """How many open hypotheses each source can confirm or rule out."""
    scores: dict[SourceName, int] = {source: 0 for source in SOURCE_ORDER}
    for hypothesis in hypotheses:
        if hypothesis.score < OPEN_HYPOTHESIS_MIN_SCORE:
            continue
        for source in sources_able_to_judge(hypothesis.name):
            scores[source] += 1
    return scores


def sources_to_ask(state: IncidentState) -> list[SourceName]:
    """Sources worth asking, best first, before the budget is considered."""
    scores = score_sources(state["hypotheses"])
    candidates: list[SourceName] = [
        source
        for source in SOURCE_ORDER
        if scores[source] > 0
        and not holds_evidence(state, source)
        and rounds_asked(state, source) < MAX_ROUNDS_PER_SOURCE
    ]

    def highest_score_first(source: SourceName) -> int:
        return -scores[source]

    return sorted(candidates, key=highest_score_first)


def affordable(state: IncidentState, sources: list[SourceName]) -> list[SourceName]:
    """The longest prefix of `sources` whose nominal cost fits the reads left in the budget."""
    reads_left = INITIAL_INVESTIGATION_BUDGET - source_read_attempts(state)
    kept: list[SourceName] = []
    for source in sources:
        reads_left -= NOMINAL_COST[source]
        if reads_left < 0:
            break
        kept.append(source)
    return kept


def rounds_asked(state: IncidentState, source: SourceName) -> int:
    """Rounds in which `source` was asked: its first read succeeded or used its last attempt."""
    first_read = SOURCE_READ_TOOLS[source][0]
    return sum(
        1
        for action in state["actions"]
        if action.tool == first_read and (action.ok or action.attempt >= READ_ATTEMPTS)
    )


def holds_evidence(state: IncidentState, source: SourceName) -> bool:
    return any(item.source in SOURCE_READ_TOOLS[source] for item in state["evidence"])


def sources_able_to_judge(hypothesis_name: str) -> list[SourceName]:
    name = hypothesis_name.lower()
    about_metrics = any(keyword in name for keyword in METRIC_KEYWORDS)
    about_deploys = any(keyword in name for keyword in DEPLOY_KEYWORDS)
    if not about_metrics and not about_deploys:
        return list(SOURCE_ORDER)
    judges: list[SourceName] = ["logs"]
    if about_metrics:
        judges.append("metrics")
    if about_deploys:
        judges.append("deploys")
    return judges
