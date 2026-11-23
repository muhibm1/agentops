"""`HandoffChatModel`: a chat model whose answers are written by a model outside this process.

The bench runs AgentOps' graph unchanged, but its model calls are answered on a subscription by
a separate model session rather than through the API. Each structured call is keyed by a hash of
its schema and its prompt (the random fence nonce blanked, so a resumed run asks the same
question). If `answers/<key>.json` exists, its text is the reply; otherwise the call writes
`requests/<key>.json` and raises `AnswerNeeded`. The graph is checkpointed, so the harness
resumes the thread once the answer is written and the node runs again from its start.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, ValidationError

NONCE = re.compile(r"\b[0-9a-f]{16}\b")


class AnswerNeeded(RuntimeError):
    def __init__(self, key: str) -> None:
        super().__init__(f"model answer needed: {key}")
        self.key = key


def text_of(message: BaseMessage) -> str:
    c = message.content
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)


class HandoffChatModel(BaseChatModel):
    folder: Path
    model: str = "claude-opus-5-5 via subscription"

    @property
    def _llm_type(self) -> str:
        return "handoff"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("The handoff model answers structured calls only.")

    def with_structured_output(
        self, schema: builtins.dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, Any]:
        assert isinstance(schema, type) and issubclass(schema, BaseModel)
        out_schema = schema

        def call(prompt: LanguageModelInput) -> Any:
            messages = self._convert_input(prompt).to_messages()
            parts = [(type(m).__name__, text_of(m)) for m in messages]
            norm = json.dumps([out_schema.__name__, [(k, NONCE.sub("NONCE", t)) for k, t in parts]])
            key = hashlib.sha256(norm.encode()).hexdigest()[:20]
            answer = self.folder / "answers" / f"{key}.json"
            if not answer.exists():
                req = self.folder / "requests" / f"{key}.json"
                req.parent.mkdir(parents=True, exist_ok=True)
                req.write_text(
                    json.dumps(
                        {
                            "key": key,
                            "schema_name": out_schema.__name__,
                            "json_schema": out_schema.model_json_schema(),
                            "messages": [{"role": k, "text": t} for k, t in parts],
                        },
                        indent=1,
                    )
                )
                raise AnswerNeeded(key)
            text = answer.read_text()
            chars_in = sum(len(t) for _, t in parts)
            raw = AIMessage(
                content=text,
                usage_metadata={
                    "input_tokens": math.ceil(chars_in / 4),
                    "output_tokens": math.ceil(len(text) / 4),
                    "total_tokens": 0,
                },
                response_metadata={"model_name": self.model},
            )
            try:
                parsed = out_schema.model_validate_json(text)
            except ValidationError as error:
                if include_raw:
                    return {"raw": raw, "parsed": None, "parsing_error": error}
                raise
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(call)
