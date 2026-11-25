"""The ReAct baseline the graph is measured against: one `create_agent` loop over six tools.

It has no graph state, approval, registry gate, preconditions, audit log or checkpointer, on
purpose. Its `apply` tool calls the source directly, so both entry points refuse any source that
is not a `SimulatorSource`: otherwise a live `EvidenceSource` would become an unapproved apply path.

With `human_approval` the same agent runs with `HumanInTheLoopMiddleware` on `apply`, and the
eval approves every pause as it does the graph's.

Each tool catches its own errors and returns them as text: the tool node re-raises anything but
an argument error, which would end the loop.
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agentops.actions.registry import Registry, default_registry, describe_actions
from agentops.evals.outcome import (
    UNDETERMINED_LABEL,
    ApplyAttempt,
    TokenUsage,
    remediation_label,
    usage_by_model_of,
)
from agentops.nodes import configured_model_id, usage_action
from agentops.sources.base import ActionRequest, ActionResult, EvidenceSource, Window
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import StrictModel, cause_catalogue

BaselineResult = Literal["remediated", "looped_then_remediated", "escalated", "failed"]

BASELINE_PROMPT = f"""You are an on-call engineer investigating a production incident.
Use the tools to read the service's logs, metrics, deployments, configuration and dependency
health, decide the root cause, and apply one action that fixes it with the apply tool.
Times are ISO 8601 in UTC, for example 2026-03-10T10:00:00Z.
Name the root cause with one label from this list: {cause_catalogue()}.
When you are done, reply with only a JSON object and no other text:
{{"root_cause": "<label>", "action": "<action applied or null>",
 "target": "<what it was applied to or null>", "params": {{}}}}"""

ANY_VALUE = TypeAdapter(Any)


def baseline_prompt(registry: Registry) -> str:
    """`BASELINE_PROMPT` plus the runbook's action names, as the graph's rca prompt lists them."""
    return f"{BASELINE_PROMPT}\n{describe_actions(registry)}"


class BaselineToolCall(StrictModel):
    """One tool call the agent made; `detail` holds the error when `ok` is false."""

    tool: str
    args: dict[str, Any]
    ok: bool
    detail: str | None = None


class BaselineAnswer(BaseModel):
    """The agent's final JSON reply; extra keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    root_cause: str = Field(min_length=1)
    action: str | None = None
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class BaselineRun(StrictModel):
    """What one baseline run did. `outcome` follows the graph's rule (`remediation_label`):
    `escalated` when no apply was accepted, `failed` when the final reply was not a valid answer."""

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
    usage_by_model: dict[str, TokenUsage] = Field(default_factory=dict)
    approvals: int = Field(default=0, ge=0)


@dataclass
class BaselineTrace:
    """Records every tool call, every apply and the evidence ids seen before the first apply."""

    tool_calls: list[BaselineToolCall] = field(default_factory=list)
    applies: list[ApplyAttempt] = field(default_factory=list)
    evidence_ids_before_apply: list[str] = field(default_factory=list)
    has_applied: bool = False
    applies_are_approved: bool = False  # every apply passed a human-approval pause
    approvals: int = 0  # approval pauses the run stopped at

    def run_tool(self, name: str, args: dict[str, Any], call: Callable[[], Any]) -> str:
        """Run `call`; return its result as JSON text, or the error as text. Never raises."""
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
    """The six tools, bound to `service` and `source`, recording into `trace`."""
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


# The decision the eval gives at every approval pause.
APPROVE = {"type": "approve"}
# More pauses than this in one run is a loop; the run stops with what it has.
MAX_APPROVALS = 10


def run_baseline(
    scenario: Scenario,
    model: BaseChatModel,
    *,
    incident_id: str,
    source: EvidenceSource | None = None,
    middleware: Sequence[Any] = (),
    human_approval: bool = False,
) -> BaselineRun:
    """Run the ReAct baseline once over `source` (a fresh `SimulatorSource` when none is given).

    `middleware` goes to `create_agent` as it is. With `human_approval`, every apply pauses and
    is approved, and the run stops after `MAX_APPROVALS` pauses. Raises `TypeError` for a source
    that is not a `SimulatorSource`.
    """
    simulator = require_simulator(source) if source is not None else SimulatorSource(scenario)
    trace = BaselineTrace(applies_are_approved=human_approval)
    tools = baseline_tools(simulator, scenario.service, incident_id, trace)
    if not human_approval:
        agent = create_agent(
            model,
            tools,
            system_prompt=baseline_prompt(default_registry()),
            middleware=tuple(middleware),
        )
        result = agent.invoke({"messages": [HumanMessage(incident_report(scenario))]})
        messages: list[BaseMessage] = result["messages"]
        return baseline_run(scenario, trace, messages, configured_model_id(model))
    # The pause needs a checkpointer; one in-memory SQLite database lives for this run only.
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        agent = create_agent(
            model,
            tools,
            system_prompt=baseline_prompt(default_registry()),
            middleware=(
                *middleware,
                HumanInTheLoopMiddleware(
                    interrupt_on={"apply": {"allowed_decisions": ["approve", "reject"]}}
                ),
            ),
            checkpointer=SqliteSaver(connection),
        )
        config: RunnableConfig = {"configurable": {"thread_id": incident_id}}
        result = agent.invoke({"messages": [HumanMessage(incident_report(scenario))]}, config)
        while result.get("__interrupt__") and trace.approvals < MAX_APPROVALS:
            trace.approvals += 1
            pending = result["__interrupt__"][0].value["action_requests"]
            decisions = {"decisions": [APPROVE for _ in pending]}
            result = agent.invoke(Command(resume=decisions), config)
    messages = result["messages"]
    return baseline_run(scenario, trace, messages, configured_model_id(model))


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
    """Hand the action straight to the simulator. `in_registry` is recorded for the evaluators;
    nothing here blocks an action the runbook does not allow."""
    request = ActionRequest.model_validate(args)
    trace.has_applied = True
    in_registry = registry.has(request.action)

    def record_attempt(ok: bool) -> None:
        trace.applies.append(
            ApplyAttempt(
                action=request.action,
                target=request.target,
                approved=trace.applies_are_approved,
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
    """The same sha256 form as `agentops.actions.registry.idempotency_key`."""
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
    scenario: Scenario,
    trace: BaselineTrace,
    messages: list[BaseMessage],
    configured_model: str | None = None,
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
        usage_by_model=usage_by_model_of(
            usage_action(reply, 0.0, configured_model) for reply in replies
        ),
        approvals=trace.approvals,
    )


def parse_answer(final_message: BaseMessage) -> tuple[BaselineAnswer | None, str | None]:
    """The answer and no failure, or no answer and the exception type name that stopped it.

    A live model often wraps its JSON in prose or a fence, so the answer is the last JSON object
    that fits `BaselineAnswer`."""
    objects = json_objects_in(final_message.text)
    for candidate in reversed(objects):
        try:
            return BaselineAnswer.model_validate(candidate), None
        except ValidationError:
            continue
    return None, ("ValidationError" if objects else "JSONDecodeError")


def json_objects_in(text: str) -> list[dict[str, Any]]:
    """Every top-level JSON object in `text`, in order, skipping anything that does not parse."""
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    position = text.find("{")
    while position != -1:
        try:
            value, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            position = text.find("{", position + 1)
            continue
        if isinstance(value, dict):
            found.append(value)
        position = text.find("{", end)
    return found


def usage_of(reply: AIMessage, key: Literal["input_tokens", "output_tokens"]) -> int:
    if reply.usage_metadata is None:
        return 0
    return reply.usage_metadata[key]
