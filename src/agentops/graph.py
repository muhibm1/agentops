"""`build_graph`: the increment-1 incident graph, START -> triage -> evidence -> rca -> END.

The model only fills the belief fields; the edges, the retries and the citation checks are code.
"""

from functools import partial

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from agentops.nodes.evidence import collect_evidence
from agentops.nodes.rca import rca
from agentops.nodes.triage import triage
from agentops.sources.base import EvidenceSource
from agentops.state import CitationError, IncidentState

# LangGraph's default predicate does not retry ValueError (so not ValidationError) nor
# RuntimeError (confirmed in langgraph/_internal/_retry.py, langgraph 1.2.12). See ADR 0002.
MODEL_NODE_RETRY = RetryPolicy(max_attempts=3, retry_on=(CitationError, ValidationError))
# Source reads change nothing, so any failure is worth exactly one more attempt.
SOURCE_NODE_RETRY = RetryPolicy(max_attempts=2, retry_on=Exception)


def build_graph(
    source: EvidenceSource,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the graph over `source` and `model`; `checkpointer` is passed to `compile`."""
    graph = StateGraph(IncidentState)
    graph.add_node("triage", partial(triage, model=model), retry_policy=MODEL_NODE_RETRY)
    graph.add_node(
        "evidence", partial(collect_evidence, source=source), retry_policy=SOURCE_NODE_RETRY
    )
    graph.add_node("rca", partial(rca, model=model), retry_policy=MODEL_NODE_RETRY)
    graph.add_edge(START, "triage")
    graph.add_edge("triage", "evidence")
    graph.add_edge("evidence", "rca")
    graph.add_edge("rca", END)
    return graph.compile(checkpointer=checkpointer)
