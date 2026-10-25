"""Which chat model answers each role, scripted or live (ADR 0001).

This is the only module that imports `langchain_anthropic` or `anthropic`, and only inside
`chat_model_class`, so the scripted demo, the tests and CI never load the live client. The key
is read from `ANTHROPIC_API_KEY` in the process environment and nowhere else.

Every client `model_factory` builds asks the spend ladder in `agentops.budget` before each call:
a recorded answer if there is one, else a paid call only while the cap has room.
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

import flywheel.spend as spend
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from agentops import budget
from agentops.fakes import scripted_baseline_model_for, scripted_model_for
from agentops.sources.scenario import Scenario

ModelRole = Literal["triage", "rca", "postmortem", "escalate", "baseline", "judge"]
ROLES: tuple[ModelRole, ...] = get_args(ModelRole)
# The roles of the graph's four model nodes; the baseline and the judge are the other two.
GRAPH_ROLES: tuple[ModelRole, ...] = ("triage", "rca", "postmortem", "escalate")
ModelName = Literal["scripted", "anthropic"]

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
ROLE_OVERRIDE_PREFIX = "AGENTOPS_MODEL_"
DEFAULT_MODEL_ID = "claude-sonnet-5-5"
DEV_MODEL_ID = "claude-haiku-4-5-20251001"
# Fixed in code so a stray ANTHROPIC_BASE_URL in the shell cannot send the key elsewhere.
API_BASE_URL = "https://api.anthropic.com"
REQUEST_TIMEOUT_SECONDS = 120
MAX_RETRIES = 2
MAX_OUTPUT_TOKENS = 4096
CACHE_TTL = "5m"
REDACTED = "[redacted]"
# Shorter values are not real keys (tests use "fake"); redacting them would blank ordinary words.
MIN_REDACTED_KEY_LENGTH = 8
# What a replaying client holds instead of a key: every answer comes from a recording, so it
# never calls the API, and a real key is never handed over where none is needed.
REPLAY_KEY = "replay-only-not-a-key"


class MissingApiKeyError(RuntimeError):
    """The live model was asked for and `ANTHROPIC_API_KEY` is unset or blank."""

    def __init__(self) -> None:
        super().__init__(
            f"{API_KEY_VARIABLE} is not set: export it in this shell to use --model anthropic."
        )


@dataclass(frozen=True)
class ModelSettings:
    """The model id each role uses."""

    model_ids: dict[ModelRole, str]

    @classmethod
    def from_environment(cls, dev: bool, environ: Mapping[str, str]) -> "ModelSettings":
        """Every role on the default id (the dev id with `dev`), then `AGENTOPS_MODEL_<ROLE>`
        overriding one role. A blank override counts as unset; unknown role names are ignored."""
        base = DEV_MODEL_ID if dev else DEFAULT_MODEL_ID
        model_ids: dict[ModelRole, str] = dict.fromkeys(ROLES, base)
        for role in ROLES:
            override = environ.get(f"{ROLE_OVERRIDE_PREFIX}{role.upper()}", "").strip()
            if override:
                model_ids[role] = override
        return cls(model_ids=model_ids)


def require_api_key(environ: Mapping[str, str]) -> str:
    """The stripped key from `ANTHROPIC_API_KEY`. Raises `MissingApiKeyError`, which names the
    variable and never a value, when it is unset or blank."""
    key = environ.get(API_KEY_VARIABLE, "").strip()
    if not key:
        raise MissingApiKeyError()
    return key


def redact_api_key(text: str, environ: Mapping[str, str] | None = None) -> str:
    """`text` with the value of `ANTHROPIC_API_KEY` replaced, so an SDK error that quotes the
    key never reaches the terminal."""
    environ = os.environ if environ is None else environ
    key = environ.get(API_KEY_VARIABLE, "").strip()
    if len(key) < MIN_REDACTED_KEY_LENGTH:
        return text
    return text.replace(key, REDACTED)


class ModelFactory(Protocol):
    """Where the graph, the baseline and the judge get their models."""

    @property
    def label(self) -> ModelName: ...

    @property
    def runs_judge(self) -> bool: ...

    def graph_models(self, scenario: Scenario) -> dict[ModelRole, BaseChatModel]: ...

    def baseline_model(self, scenario: Scenario) -> BaseChatModel: ...

    def judge_model(self) -> BaseChatModel: ...

    def baseline_middleware(self) -> Sequence[Any]: ...

    def model_ids(self) -> dict[ModelRole, str]: ...

    @property
    def guard(self) -> spend.Guard | None: ...

    @property
    def recorder(self) -> spend.Recorder | None: ...


class ScriptedModels:
    """The fake models that replay each scenario's script. The judge has no script, so it never
    runs on them. They spend nothing, so they have no guard and no recordings."""

    label: ModelName = "scripted"
    runs_judge = False
    guard: spend.Guard | None = None
    recorder: spend.Recorder | None = None

    def graph_models(self, scenario: Scenario) -> dict[ModelRole, BaseChatModel]:
        model = scripted_model_for(scenario)
        return dict.fromkeys(GRAPH_ROLES, model)  # type: ignore[return-value]

    def baseline_model(self, scenario: Scenario) -> BaseChatModel:
        return scripted_baseline_model_for(scenario)

    def judge_model(self) -> BaseChatModel:
        raise RuntimeError(
            "The scripted models have no judge script; the judge needs a live model."
        )

    def baseline_middleware(self) -> Sequence[Any]:
        return ()

    def model_ids(self) -> dict[ModelRole, str]:
        return dict.fromkeys(ROLES, "scripted")


@dataclass(frozen=True)
class LiveModels:
    """One ChatAnthropic per role, built from `settings` with the key passed explicitly.

    The scenario handed to `graph_models` and `baseline_model` is never read, so no ground truth
    or scenario id can reach a prompt through the factory.

    `guard` and `recorder` are the spend ladder every client asks before a call; `model_factory`
    always sets both. Built by hand without either, as the live smoke test does, the clients call
    the API directly."""

    settings: ModelSettings
    api_key: SecretStr = field(repr=False)
    label: ModelName = "anthropic"
    runs_judge = True
    guard: spend.Guard | None = field(default=None, repr=False)
    recorder: spend.Recorder | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.get_secret_value().strip():
            raise MissingApiKeyError()

    def graph_models(self, scenario: Scenario) -> dict[ModelRole, BaseChatModel]:
        return {role: self.client(role) for role in GRAPH_ROLES}

    def baseline_model(self, scenario: Scenario) -> BaseChatModel:
        return self.client("baseline")

    def judge_model(self) -> BaseChatModel:
        return self.client("judge")

    def baseline_middleware(self) -> Sequence[Any]:
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

        return (AnthropicPromptCachingMiddleware(ttl=CACHE_TTL),)

    def model_ids(self) -> dict[ModelRole, str]:
        return dict(self.settings.model_ids)

    def client(self, role: ModelRole) -> BaseChatModel:
        """The client for `role`. With a guard or recordings it asks them before every call, and
        never streams: LangChain skips the cache slot for a streamed call."""
        ladder: dict[str, Any] = {}
        if self.guard is not None or self.recorder is not None:
            ladder = {
                "cache": budget.answer_cache(self.guard, self.recorder, role),
                "disable_streaming": True,
            }
        return chat_model_class()(
            model=self.settings.model_ids[role],
            api_key=self.api_key,
            base_url=API_BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
            max_tokens=MAX_OUTPUT_TOKENS,
            **ladder,
        )


def chat_model_class() -> Any:
    """`ChatAnthropic`, imported on first use so nothing else loads the live client."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic


def model_factory(
    model: ModelName, dev: bool, environ: Mapping[str, str] | None = None
) -> ModelFactory:
    """The factory for `--model` and `--dev`. Raises `MissingApiKeyError` for `anthropic`
    without a key, and `budget.SpendSettingError` for a spend setting it cannot use, before any
    client is built. With `AGENTOPS_REPLAY=replay` no key is read and none is passed on."""
    if model == "scripted":
        return ScriptedModels()
    environ = os.environ if environ is None else environ
    guard = budget.guard_from(environ)
    recorder = budget.recorder_from(environ)
    key = REPLAY_KEY if budget.is_replaying(recorder) else require_api_key(environ)
    return LiveModels(
        ModelSettings.from_environment(dev, environ),
        SecretStr(key),
        guard=guard,
        recorder=recorder,
    )
