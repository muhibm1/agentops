"""`build_graph`: the investigation half of the incident graph.

    START -> triage --route_to_sources--> logs | metrics | deploys | merge
    logs, metrics, deploys -> merge --after_merge--> rca | escalate | the sources again
    rca --after_rca--> END (approval-ready) | escalate
    escalate -> END

The model only fills the belief fields; every edge, retry and budget decision is code.
"""

from functools import partial

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from agentops.nodes.escalate import escalate
from agentops.nodes.evidence import deploys, logs, metrics
from agentops.nodes.merge import after_merge, merge
from agentops.nodes.rca import after_rca, rca
from agentops.nodes.router import route_to_sources
from agentops.nodes.triage import triage
from agentops.sources.base import EvidenceSource
from agentops.state import CitationError, IncidentState

# LangGraph's default predicate does not retry ValueError (so not ValidationError) nor
# RuntimeError (confirmed in langgraph/_internal/_retry.py, langgraph 1.2.12). See ADR 0002.
# Source reads have no graph-level retry: each evidence node retries its own read (ADR 0001).
MODEL_NODE_RETRY = RetryPolicy(max_attempts=3, retry_on=(CitationError, ValidationError))

EVIDENCE_NODES = ("logs", "metrics", "deploys")


def build_graph(
    source: EvidenceSource,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the graph over `source` and `model`; `checkpointer` is passed to `compile`."""
    graph = StateGraph(IncidentState)
    graph.add_node("triage", partial(triage, model=model), retry_policy=MODEL_NODE_RETRY)
    graph.add_node("logs", partial(logs, source=source))
    graph.add_node("metrics", partial(metrics, source=source))
    graph.add_node("deploys", partial(deploys, source=source))
    graph.add_node("merge", merge)
    graph.add_node("rca", partial(rca, model=model), retry_policy=MODEL_NODE_RETRY)
    # Escalate retries inside the node and falls back to a summary written by code, so a run never
    # ends without a hand-off.
    graph.add_node("escalate", partial(escalate, model=model))

    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_to_sources, [*EVIDENCE_NODES, "merge"])
    for evidence_node in EVIDENCE_NODES:
        graph.add_edge(evidence_node, "merge")
    graph.add_conditional_edges("merge", after_merge, ["rca", "escalate", *EVIDENCE_NODES])
    graph.add_conditional_edges("rca", after_rca, ["escalate", END])
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=checkpointer)
