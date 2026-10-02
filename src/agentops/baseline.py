"""The ReAct baseline the graph is measured against: one `create_agent` loop over six tools.

It has no graph state, approval, registry gate, preconditions, audit log or checkpointer, on
purpose: it is the naive agent the design compares against. Its `apply` tool calls the source
directly, so both entry points refuse any source that is not a `SimulatorSource` (D19). The
module ships in the installed package, and without that check the first live `EvidenceSource`
would turn it into an unapproved apply path against a real system.

`create_agent` accepts `ScriptedChatModel` with no provider profile and calls its `bind_tools`
(confirmed by `tests/test_baseline.py`). Its tool node re-raises any tool exception other than
an argument error (confirmed in `langgraph/prebuilt/tool_node.py`,
`_default_handle_tool_errors`), so each tool here catches its own errors and returns them to the
agent as text, which keeps the loop going.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agentops.actions.registry import Registry, default_registry
from agentops.evals.outcome import UNDETERMINED_LABEL, ApplyAttempt, remediation_label
from agentops.sources.base import ActionRequest, ActionResult, EvidenceSource, Window
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import StrictModel

BaselineResult = Literal["remediated", "looped_then_remediated", "escalated", "failed"]

BASELINE_PROMPT = """You are an on-call engineer investigating a production incident.
Use the tools to read the service's logs, metrics, deployments, configuration and dependency
health, decide the root cause, and apply one action that fixes it with the apply tool.
Times are ISO 8601 in UTC, for example 2026-03-10T10:00:00Z.
When you are done, reply with only a JSON object and no other text:
{"root_cause": "<snake_case label>", "action": "<action applied or null>",
 "target": "<what it was applied to or null>", "params": {}}"""

ANY_VALUE = TypeAdapter(Any)


class BaselineToolCall(StrictModel):
    """One tool call the agent made; `detail` holds the error when `ok` is false."""

    tool: str
    args: dict[str, Any]
    ok: bool
    detail: str | None = None


class BaselineAnswer(BaseModel):
    """The agent's final JSON reply. Keys beyond these four are ignored: a naive agent often
    adds commentary, and the answer is still readable without it."""

    model_config = ConfigDict(extra="ignore")

    root_cause: str = Field(min_length=1)
    action: str | None = None
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class BaselineRun(StrictModel):
    """What one baseline run did. `outcome` is `remediated` when one apply was accepted by the
    source, `looped_then_remediated` when two or more were (the graph's rule, see
    `remediation_label`), `escalated` when the agent answered without one, and `failed` when
    its final reply was not a valid answer."""

    scenario_id: str
    outcome: BaselineResult
    # The exception type name when `outcome` is `failed`.
    failure: str | None
    answer: BaselineAnswer | None
    root_cause_label: str
    tool_calls: list[BaselineToolCall]
    applies: list[ApplyAttempt]
    # Evidence ids the tools returned before the first apply, or before the answer if none.
    evidence_ids_before_apply: list[str]
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


@dataclass
class BaselineTrace:
    """Records every tool call, every apply and the evidence ids seen before the first apply."""

    tool_calls: list[BaselineToolCall] = field(default_factory=list)
    applies: list[ApplyAttempt] = field(default_factory=list)
    evidence_ids_before_apply: list[str] = field(default_factory=list)
    has_applied: bool = False

    def run_tool(self, name: str, args: dict[str, Any], call: Callable[[], Any]) -> str:
        """Runs `call` and returns its result as JSON text, or the error as text for the
        agent to read. Never raises."""
        try:
            result = call()
        except Exception as error:  # every tool error goes back to the agent, by design
            detail = f"{type(error).__name__}: {error}"
            self.tool_calls.append(BaselineToolCall(tool=name, args=args, ok=False, detail=detail))
            return f"Tool error: {detail}"
        self.tool_calls.append(BaselineToolCall(tool=name, args=args, ok=True))
        if not self.has_applied:
            self.evidence_ids_before_apply.extend(evidence_ids_in(result))
        return ANY_VALUE.dump_json(result).decode("utf-8")


def baseline_tools(
    source: EvidenceSource, service: str, incident_id: str, trace: BaselineTrace
) -> list[BaseTool]:
    """The six tools, bound to `service` and `source`, recording into `trace`.

    Raises `TypeError` when `source` is not a `SimulatorSource` (D19).
    """
    simulator = require_simulator(source)
    registry = default_registry()

    @tool
    def get_logs(start: str, end: str, query: str | None = None) -> str:
        """Log lines of the service between start and end, optionally only those containing
        the query text."""
        args = {"start": start, "end": end, "query": query}
        window = window_between(start, end)
        return trace.run_tool(
            "get_logs", args, lambda: simulator.get_logs(service, window(), query)
        )

    @tool
    def get_metrics(names: list[str], start: str, end: str) -> str:
        """The named metric series of the service between start and end."""
        args = {"names": names, "start": start, "end": end}
        window = window_between(start, end)
        return trace.run_tool(
            "get_metrics", args, lambda: simulator.get_metrics(service, names, window())
        )

    @tool
    def get_deployments(start: str, end: str) -> str:
        """Deployments of the service between start and end."""
        window = window_between(start, end)
        return trace.run_tool(
            "get_deployments",
            {"start": start, "end": end},
            lambda: simulator.get_deployments(service, window()),
        )

    @tool
    def get_config() -> str:
        """The service's current configuration."""
        return trace.run_tool("get_config", {}, lambda: simulator.get_config(service))

    @tool
    def get_dependency_health() -> str:
        """The health of the service's dependencies."""
        return trace.run_tool(
            "get_dependency_health", {}, lambda: simulator.get_dependency_health(service)
        )

    @tool
    def apply(action: str, target: str, params: dict[str, Any] | None = None) -> str:
        """Apply a remediation action to a target, with optional parameters."""
        args = {"action": action, "target": target, "params": params or {}}
        return trace.run_tool(
            "apply",
            args,
            lambda: apply_unchecked(simulator, registry, trace, incident_id, args),
        )

    return [get_logs, get_metrics, get_deployments, get_config, get_dependency_health, apply]


def run_baseline(
    scenario: Scenario,
    model: BaseChatModel,
    *,
    incident_id: str,
    source: EvidenceSource | None = None,
) -> BaselineRun:
    """Runs the ReAct baseline once over `source` (a fresh `SimulatorSource` of `scenario`
    when none is given) and returns what it did.

    Raises `TypeError` when `source` is not a `SimulatorSource` (D19), before the model or the
    source is called. An exception from the model or the agent loop propagates.
    """
    simulator = require_simulator(source) if source is not None else SimulatorSource(scenario)
    trace = BaselineTrace()
    tools = baseline_tools(simulator, scenario.service, incident_id, trace)
    agent = create_agent(model, tools, system_prompt=BASELINE_PROMPT)
    result = agent.invoke({"messages": [HumanMessage(incident_report(scenario))]})
    messages: list[BaseMessage] = result["messages"]
    return baseline_run(scenario, trace, messages)


def require_simulator(source: EvidenceSource) -> SimulatorSource:
    if not isinstance(source, SimulatorSource):
        raise TypeError(
            "The baseline applies actions with no approval or allowlist, so it runs only on a "
            f"SimulatorSource; it was given a {type(source).__name__}."
        )
    return source


def window_between(start: str, end: str) -> Callable[[], Window]:
    """Defers validation into the tool call, so a bad time is reported to the agent."""
    return lambda: Window.model_validate({"start": start, "end": end})


def apply_unchecked(
    simulator: SimulatorSource,
    registry: Registry,
    trace: BaselineTrace,
    incident_id: str,
    args: dict[str, Any],
) -> ActionResult:
    """Hands the action straight to the simulator. `in_registry` is recorded for the
    evaluators only; nothing here blocks an action the runbook does not allow."""
    request = ActionRequest.model_validate(args)
    trace.has_applied = True
    in_registry = registry.has(request.action)

    def record_attempt(ok: bool) -> None:
        trace.applies.append(
            ApplyAttempt(
                action=request.action,
                target=request.target,
                approved=False,
                in_registry=in_registry,
                ok=ok,
            )
        )

    try:
        result = simulator.apply(request, baseline_idempotency_key(incident_id, request))
    except Exception:
        record_attempt(ok=False)
        raise
    record_attempt(ok=result.ok)
    return result


def baseline_idempotency_key(incident_id: str, request: ActionRequest) -> str:
    """sha256 of the incident id and the sorted-key JSON of action, target and params, the same
    form `agentops.actions.registry.idempotency_key` uses for the graph."""
    canonical = json.dumps(
        {"action": request.action, "target": request.target, "params": request.params},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((incident_id + canonical).encode("utf-8")).hexdigest()


def evidence_ids_in(result: Any) -> list[str]:
    """The `id` of every signal in a tool result; configuration carries none."""
    if not isinstance(result, list):
        return []
    return [item.id for item in result if isinstance(getattr(item, "id", None), str)]


def incident_report(scenario: Scenario) -> str:
    symptoms = "\n".join(f"- {symptom}" for symptom in scenario.symptoms)
    return (
        f"Service: {scenario.service}\n"
        f"Incident opened at: {scenario.incident_time.isoformat()}\n"
        f"Symptoms:\n{symptoms}"
    )


def baseline_run(
    scenario: Scenario, trace: BaselineTrace, messages: list[BaseMessage]
) -> BaselineRun:
    replies = [message for message in messages if isinstance(message, AIMessage)]
    answer, failure = parse_answer(messages[-1])
    if answer is None:
        outcome: BaselineResult = "failed"
    elif any(attempt.ok for attempt in trace.applies):
        outcome = remediation_label(trace.applies)
    else:
        outcome = "escalated"
    return BaselineRun(
        scenario_id=scenario.id,
        outcome=outcome,
        failure=failure,
        answer=answer,
        root_cause_label=answer.root_cause if answer is not None else UNDETERMINED_LABEL,
        tool_calls=trace.tool_calls,
        applies=trace.applies,
        evidence_ids_before_apply=list(dict.fromkeys(trace.evidence_ids_before_apply)),
        model_calls=len(replies),
        input_tokens=sum(usage_of(reply, "input_tokens") for reply in replies),
        output_tokens=sum(usage_of(reply, "output_tokens") for reply in replies),
    )


def parse_answer(final_message: BaseMessage) -> tuple[BaselineAnswer | None, str | None]:
    """The answer and no failure, or no answer and the exception type name that stopped it."""
    try:
        return BaselineAnswer.model_validate(json.loads(final_message.text)), None
    except (json.JSONDecodeError, ValidationError) as error:
        return None, type(error).__name__


def usage_of(reply: AIMessage, key: Literal["input_tokens", "output_tokens"]) -> int:
    if reply.usage_metadata is None:
        return 0
    return reply.usage_metadata[key]
