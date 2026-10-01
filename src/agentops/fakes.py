"""A chat model that replays a fixed script, so tests and the demo run with no API key.

It reads no environment variable and opens no socket: every answer comes from the script.
"""

import builtins
import json
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field, PrivateAttr, SkipValidation

from agentops.sources.scenario import Scenario

ScriptEntry = BaseModel | dict[str, Any] | str


class ScriptExhausted(RuntimeError):
    """The model was called more times than its script has entries."""


class ScriptedChatModel(BaseChatModel):
    """Returns `script` entries in order, one per call, and records every prompt in `calls`.

    Entries may be Pydantic models, dicts or JSON text; all are stored as JSON text when the
    model is built, so a malformed entry fails at the point a schema validates it.
    """

    # Validation is skipped so Pydantic cannot coerce a dict entry into a bare BaseModel; the
    # entries are only ever turned into JSON text below.
    script: SkipValidation[list[ScriptEntry]]
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    _pending_outputs: list[str] = PrivateAttr(default_factory=list)

    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        self._pending_outputs = [entry_as_json_text(entry) for entry in self.script]

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
        if not self._pending_outputs:
            raise ScriptExhausted(
                f"The scripted model was called {len(self.calls)} times "
                f"but its script has only {len(self.script)} entries."
            )
        content = self._pending_outputs.pop(0)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def with_structured_output(
        self,
        schema: builtins.dict[str, Any] | type,
        *,
        include_raw: bool = False,
        method: str = "json_schema",
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, builtins.dict[str, Any] | BaseModel]:
        """Returns a runnable that calls this model and validates its reply against `schema`.

        Raises `pydantic.ValidationError` when the scripted reply does not fit `schema`.
        """
        if include_raw:
            raise NotImplementedError("The scripted model does not return raw messages.")
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError("The scripted model needs a Pydantic model class as its schema.")
        output_schema = schema

        def call_model_and_validate(prompt: LanguageModelInput) -> BaseModel:
            reply = self.invoke(prompt)
            return output_schema.model_validate_json(reply_text(reply))

        return RunnableLambda(call_model_and_validate)


def scripted_model_for(scenario: Scenario) -> ScriptedChatModel:
    """A model that answers triage, then rca, with the scenario's own scripted outputs."""
    return ScriptedChatModel(
        script=[scenario.scripted_outputs.triage, scenario.scripted_outputs.rca]
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
