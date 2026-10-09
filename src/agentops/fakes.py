"""A chat model that replays a fixed script, so tests and the demo run with no API key.

It reads no environment variable and opens no socket: every answer comes from the script.
"""

import builtins
import json
import math
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field, PrivateAttr, SkipValidation, ValidationError

from agentops.sources.scenario import Scenario
from agentops.state import Escalation, Postmortem, RcaOutput, Triage

# Equals the model-node retry policy's max_attempts; the graph test asserts the two agree.
DEFAULT_ATTEMPTS = 3
# The rca prompt line that lists actions already applied; it selects `rca_after_action`.
ACTIONS_APPLIED_MARKER = "Actions already applied:"
# The model id the fake reports on every reply; the price table has no entry for it.
SCRIPTED_MODEL_ID = "scripted"
# Characters per token in the fake's usage stamp: deterministic and non-zero, not a tokenizer.
CHARACTERS_PER_TOKEN = 4

ScriptEntry = BaseModel | dict[str, Any] | str


class ScriptExhausted(RuntimeError):
    """The model was called more times than its script has entries."""


class ScriptedChatModel(BaseChatModel):
    """Returns scripted answers and records every prompt in `calls`.

    `script` is one shared queue, consumed in call order whatever is asked. `script_by_schema` is
    one queue per requested schema: a request for that schema takes the next entry from its own
    queue, so a node that is retried meets its own answer again whatever other nodes consumed.
    A schema with a queue never reads the shared `script`.

    Entries may be Pydantic models, dicts or JSON text; all are stored as JSON text when the
    model is built, so a malformed entry fails at the point a schema validates it.
    """

    # Validation is skipped so Pydantic cannot coerce a dict entry into a bare BaseModel; the
    # entries are only ever turned into JSON text below.
    script: SkipValidation[list[ScriptEntry]] = Field(default_factory=list)
    # Entries answered only to a request for that schema, one queue per schema, so a node that
    # is retried meets its own answer again whatever the other nodes consumed.
    script_by_schema: SkipValidation[dict[type, list[ScriptEntry]]] = Field(default_factory=dict)
    # Per schema, queues chosen by a marker: a request whose prompt has a line starting with the
    # marker takes the next entry from that marker's queue instead of the schema's own queue.
    answers_by_prompt_marker: SkipValidation[dict[type, dict[str, list[ScriptEntry]]]] = Field(
        default_factory=dict
    )
    # Whole replies, tool calls included, answered before any `script` entry; a ReAct agent
    # needs replies that call tools, which a text entry cannot express.
    messages: SkipValidation[list[AIMessage]] = Field(default_factory=list)
    # The names of the tools passed to `bind_tools`, in order, for tests to read.
    bound_tools: list[str] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    # The configured id, read like ChatAnthropic's `model` when an `Action` names who answered.
    model: str = SCRIPTED_MODEL_ID
    _pending_messages: list[AIMessage] = PrivateAttr(default_factory=list)
    _pending_outputs: list[str] = PrivateAttr(default_factory=list)
    _pending_by_schema: dict[type, list[str]] = PrivateAttr(default_factory=dict)
    _pending_by_marker: dict[type, dict[str, list[str]]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        self._pending_messages = list(self.messages)
        self._pending_outputs = [entry_as_json_text(entry) for entry in self.script]
        self._pending_by_schema = {
            schema: [entry_as_json_text(entry) for entry in entries]
            for schema, entries in self.script_by_schema.items()
        }
        self._pending_by_marker = {
            schema: {
                marker: [entry_as_json_text(entry) for entry in entries]
                for marker, entries in queues.items()
            }
            for schema, queues in self.answers_by_prompt_marker.items()
        }

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if self._pending_messages:
            reply = with_usage(self._pending_messages.pop(0), messages)
            return ChatResult(generations=[ChatGeneration(message=reply)])
        if not self._pending_outputs:
            raise ScriptExhausted(
                f"The scripted model was called {len(self.calls)} times but it has only "
                f"{len(self.messages)} messages and {len(self.script)} script entries."
            )
        content = self._pending_outputs.pop(0)
        reply = with_usage(AIMessage(content=content), messages)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(
        self,
        tools: Sequence[builtins.dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Records the tool names and returns this model: the script decides every tool call."""
        self.bound_tools = [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        return self

    def with_structured_output(
        self,
        schema: builtins.dict[str, Any] | type,
        *,
        include_raw: bool = False,
        method: str = "json_schema",
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, builtins.dict[str, Any] | BaseModel]:
        """Returns a runnable that calls this model and validates its reply against `schema`.

        Raises `pydantic.ValidationError` when the scripted reply does not fit `schema`. With
        `include_raw=True` it returns `{"raw", "parsed", "parsing_error"}` instead, as
        ChatAnthropic does: a reply that does not fit leaves `parsed` None and nothing raises.
        """
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError("The scripted model needs a Pydantic model class as its schema.")
        output_schema = schema

        def next_reply(prompt: LanguageModelInput) -> AIMessage:
            messages = self._convert_input(prompt).to_messages()
            marker = self._marker_in(output_schema, messages)
            if marker is not None:
                text = self._next_for_marker(output_schema, marker, messages)
                return with_usage(AIMessage(content=text), messages)
            if output_schema in self._pending_by_schema:
                text = self._next_for_schema(output_schema, prompt)
                return with_usage(AIMessage(content=text), messages)
            reply = self.invoke(prompt)
            return with_usage(AIMessage(content=reply_text(reply)), messages)

        def call_model_and_validate(prompt: LanguageModelInput) -> BaseModel:
            return output_schema.model_validate_json(reply_text(next_reply(prompt)))

        def call_model_with_raw(prompt: LanguageModelInput) -> builtins.dict[str, Any]:
            raw = next_reply(prompt)
            try:
                parsed = output_schema.model_validate_json(reply_text(raw))
            except ValidationError as error:
                return {"raw": raw, "parsed": None, "parsing_error": error}
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        if include_raw:
            return RunnableLambda(call_model_with_raw)
        return RunnableLambda(call_model_and_validate)

    def _next_for_schema(self, schema: type, prompt: LanguageModelInput) -> str:
        self.calls.append(self._convert_input(prompt).to_messages())
        queue = self._pending_by_schema[schema]
        if not queue:
            raise ScriptExhausted(
                f"The scripted model has no entries left for {schema.__name__} "
                f"after {len(self.calls)} calls."
            )
        return queue.pop(0)

    def _marker_in(self, schema: type, messages: list[BaseMessage]) -> str | None:
        """The first of `schema`'s markers that starts a line of the prompt, or None."""
        lines = prompt_lines(messages)
        for marker in self._pending_by_marker.get(schema, {}):
            if any(line.startswith(marker) for line in lines):
                return marker
        return None

    def _next_for_marker(self, schema: type, marker: str, messages: list[BaseMessage]) -> str:
        self.calls.append(messages)
        queue = self._pending_by_marker[schema][marker]
        if not queue:
            raise ScriptExhausted(
                f"The scripted model has no entries left for {schema.__name__} prompts "
                f"with the line {marker!r} after {len(self.calls)} calls."
            )
        return queue.pop(0)


def scripted_model_for(scenario: Scenario, attempts: int = DEFAULT_ATTEMPTS) -> ScriptedChatModel:
    """A model that answers each node from the scenario's scripted outputs, once per attempt.

    The escalate, postmortem and after-action rca queues are present only when the scenario
    scripts them, so a scenario that never escalates has no `Escalation` queue and a stray call
    fails loudly. An rca prompt holding the line `Actions already applied:` is answered from
    `rca_after_action` when the scenario has one, and from the rca queue otherwise.
    """
    outputs = scenario.scripted_outputs
    script_by_schema: dict[type, list[ScriptEntry]] = {
        Triage: [outputs.triage] * attempts,
        RcaOutput: [outputs.rca] * attempts,
    }
    if outputs.escalate is not None:
        script_by_schema[Escalation] = [outputs.escalate] * attempts
    if outputs.postmortem is not None:
        script_by_schema[Postmortem] = [outputs.postmortem] * attempts
    answers_by_prompt_marker: dict[type, dict[str, list[ScriptEntry]]] = {}
    if outputs.rca_after_action is not None:
        answers_by_prompt_marker[RcaOutput] = {
            ACTIONS_APPLIED_MARKER: [outputs.rca_after_action] * attempts
        }
    return ScriptedChatModel(
        script_by_schema=script_by_schema, answers_by_prompt_marker=answers_by_prompt_marker
    )


def scripted_baseline_model_for(scenario: Scenario) -> ScriptedChatModel:
    """A model that replays the scenario's baseline script: one tool call per step, ids
    `call-1`, `call-2` and so on, then the answer as JSON text.

    Raises `ValueError` naming the scenario when it has no `scripted_outputs.baseline`.
    """
    script = scenario.scripted_outputs.baseline
    if script is None:
        raise ValueError(
            f"Scenario {scenario.id!r} has no scripted_outputs.baseline, so the baseline "
            "cannot run on the scripted model."
        )
    tool_calls = [
        AIMessage(
            content="",
            tool_calls=[{"name": step.tool, "args": step.args, "id": f"call-{number}"}],
        )
        for number, step in enumerate(script.steps, start=1)
    ]
    return ScriptedChatModel(messages=tool_calls, script=[script.answer])


def prompt_lines(messages: list[BaseMessage]) -> list[str]:
    """Every line of every message, whether its content is text or a list of text blocks."""
    return [line.strip() for message in messages for line in message_text(message).splitlines()]


def message_text(message: BaseMessage) -> str:
    """The text of a message whose content is a string or a list of text blocks, blocks joined
    by a newline; a non-text block contributes nothing."""
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        block["text"] if isinstance(block, dict) and isinstance(block.get("text"), str) else ""
        for block in message.content
        if isinstance(block, (dict, str))
    )


def with_usage(reply: AIMessage, prompt: list[BaseMessage]) -> AIMessage:
    """`reply` stamped with the fake's usage, about four characters to a token and never zero,
    and the model id `scripted`, as a live reply carries its own."""
    prompt_characters = sum(len(message_text(message)) for message in prompt)
    reply_characters = len(message_text(reply)) + len(json.dumps(reply.tool_calls))
    return reply.model_copy(
        update={
            "usage_metadata": {
                "input_tokens": max(1, math.ceil(prompt_characters / CHARACTERS_PER_TOKEN)),
                "output_tokens": max(1, math.ceil(reply_characters / CHARACTERS_PER_TOKEN)),
                "total_tokens": 0,
                "input_token_details": {"cache_read": 0, "cache_creation": 0},
            },
            "response_metadata": {"model_name": SCRIPTED_MODEL_ID},
        }
    )


def entry_as_json_text(entry: ScriptEntry) -> str:
    if isinstance(entry, BaseModel):
        return entry.model_dump_json()
    if isinstance(entry, dict):
        return json.dumps(entry)
    return entry


def reply_text(reply: BaseMessage) -> str:
    if not isinstance(reply.content, str):
        raise TypeError("The scripted model always replies with text content.")
    return reply.content
