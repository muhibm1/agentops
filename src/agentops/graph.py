"""`build_graph`: the incident graph from triage to a resolved or escalated incident, compiled
with a checkpointer. `build_state_graph` returns the same graph uncompiled.

    START -> triage --route_to_sources--> logs | metrics | deploys | merge
    logs, metrics, deploys -> merge --after_merge--> rca | escalate | the sources again
    rca --after_rca--> approval | escalate
    approval --after_approval--> execute | approval (refused answer) | the sources (reject)
    execute --after_execute--> verify | escalate | the sources (failed precondition)
    verify --after_verify--> postmortem | escalate | the sources (symptoms remain)
    postmortem -> END, escalate -> END

The model only fills the belief fields; every edge, retry and budget decision is code. `execute`
is reachable only from `approval`, and the graph cannot be built without a checkpointer, because
`approval` pauses the run with `interrupt()` (spec R12, R14).
"""

from collections.abc import Mapping
from functools import partial

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from agentops import state as state_models
from agentops.actions.audit import AuditLog
from agentops.actions.registry import Registry
from agentops.models import GRAPH_ROLES, ModelRole
from agentops.nodes.approval import after_approval, approval
from agentops.nodes.escalate import escalate
from agentops.nodes.evidence import deploys, logs, metrics
from agentops.nodes.execute import after_execute, execute
from agentops.nodes.merge import after_merge, merge
from agentops.nodes.postmortem import postmortem
from agentops.nodes.rca import after_rca, rca
from agentops.nodes.router import route_to_sources
from agentops.nodes.triage import triage
from agentops.nodes.verify import after_verify, verify
from agentops.sources.base import EvidenceSource
from agentops.state import CitationError, IncidentState

# LangGraph's default predicate does not retry ValueError (so not ValidationError) nor
# RuntimeError (confirmed in langgraph/_internal/_retry.py, langgraph 1.2.12).
# See increment-1 ADR 0002.
# Source reads have no graph-level retry: each evidence node retries its own read
# (increment-2 ADR 0001).
MODEL_NODE_RETRY = RetryPolicy(max_attempts=3, retry_on=(CitationError, ValidationError))

EVIDENCE_NODES = ("logs", "metrics", "deploys")
# Where an edge goes when it sends the run back to investigate: the router's own targets.
INVESTIGATE_AGAIN = (*EVIDENCE_NODES, "merge")
# One chat model for every node, or one per role (`agentops.models`).
GraphModels = BaseChatModel | Mapping[ModelRole, BaseChatModel]
# The state's own models, the only application types a checkpoint may bring back to life.
CHECKPOINT_MODEL_TYPES = (
    state_models.Hypothesis,
    state_models.RCA,
    state_models.Remediation,
    state_models.Escalation,
    state_models.Postmortem,
    state_models.Evidence,
    state_models.Action,
    state_models.Approval,
    state_models.ExecutionRecord,
)


def build_graph(
    source: EvidenceSource,
    model: GraphModels,
    registry: Registry,
    audit: AuditLog,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """Compile the graph over `source` and `model`, with `registry` deciding what may run and
    `audit` recording every proposal, decision, apply, verdict and hand-off.

    Raises ValueError when `checkpointer` is None: the approval pause needs one.
    """
    if checkpointer is None:
        raise ValueError(
            "build_graph needs a checkpointer: the approval step pauses the run and resumes it "
            "from the last checkpoint."
        )
    graph = build_state_graph(source, model, registry, audit)
    return graph.compile(checkpointer=checkpointer)


def build_state_graph(
    source: EvidenceSource,
    model: GraphModels,
    registry: Registry,
    audit: AuditLog,
) -> StateGraph:
    """The same graph as `build_graph`, uncompiled, for a caller that compiles it itself. Every
    run that can reach `approval` needs a checkpointer at compile time; `build_graph` enforces
    that, this function does not."""
    graph = StateGraph(IncidentState)
    add_investigation_nodes(graph, source, model, registry, audit)
    add_action_nodes(graph, source, model, registry, audit)

    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_to_sources, list(INVESTIGATE_AGAIN))
    for evidence_node in EVIDENCE_NODES:
        graph.add_edge(evidence_node, "merge")
    graph.add_conditional_edges("merge", after_merge, ["rca", "escalate", *EVIDENCE_NODES])
    graph.add_conditional_edges("rca", after_rca, ["approval", "escalate"])
    graph.add_conditional_edges(
        "approval", after_approval, ["approval", "execute", *INVESTIGATE_AGAIN]
    )
    graph.add_conditional_edges(
        "execute", after_execute, ["verify", "escalate", *INVESTIGATE_AGAIN]
    )
    graph.add_conditional_edges(
        "verify", after_verify, ["postmortem", "escalate", *INVESTIGATE_AGAIN]
    )
    graph.add_edge("postmortem", END)
    graph.add_edge("escalate", END)
    return graph


def checkpoint_serializer() -> JsonPlusSerializer:
    """A checkpoint serializer that revives LangGraph's safe types and the state's models only.

    Pass it as `serde=` to any checkpointer given to `build_graph`. Without it LangGraph revives
    any type a checkpoint names, with a warning; with it, a checkpoint file that names any other
    type cannot construct it (langgraph-checkpoint 4.2.0, `allowed_msgpack_modules`).
    """
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_MODEL_TYPES)


def add_investigation_nodes(
    graph: StateGraph,
    source: EvidenceSource,
    model: GraphModels,
    registry: Registry,
    audit: AuditLog,
) -> None:
    """Triage, the three evidence nodes, merge, rca and escalate."""
    models = models_by_role(model)
    graph.add_node("triage", partial(triage, model=models["triage"]), retry_policy=MODEL_NODE_RETRY)
    graph.add_node("logs", partial(logs, source=source))
    graph.add_node("metrics", partial(metrics, source=source))
    graph.add_node("deploys", partial(deploys, source=source))
    graph.add_node("merge", merge)
    graph.add_node(
        "rca",
        partial(rca, model=models["rca"], registry=registry, audit=audit),
        retry_policy=MODEL_NODE_RETRY,
    )
    # Escalate and postmortem retry inside the node and fall back to a text written by code, so a
    # run never ends without a hand-off or a postmortem.
    graph.add_node("escalate", partial(escalate, model=models["escalate"], audit=audit))


def add_action_nodes(
    graph: StateGraph,
    source: EvidenceSource,
    model: GraphModels,
    registry: Registry,
    audit: AuditLog,
) -> None:
    """Approval, execute, verify and postmortem."""
    graph.add_node("approval", partial(approval, registry=registry, audit=audit))
    graph.add_node("execute", partial(execute, source=source, registry=registry, audit=audit))
    graph.add_node("verify", partial(verify, source=source, audit=audit, registry=registry))
    graph.add_node("postmortem", partial(postmortem, model=models_by_role(model)["postmortem"]))


def models_by_role(model: GraphModels) -> dict[ModelRole, BaseChatModel]:
    """The model each graph node uses: one model fans out to every role. Raises KeyError naming
    the first graph role a mapping leaves out."""
    if isinstance(model, BaseChatModel):
        return dict.fromkeys(GRAPH_ROLES, model)
    return {role: model[role] for role in GRAPH_ROLES}
