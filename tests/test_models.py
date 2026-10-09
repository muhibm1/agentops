"""E1, E3 and E33: the live models per role, their ids, the fixed API address and the key
(R1, R4)."""

from typing import Any

import pytest
from pydantic import SecretStr

from agentops import models
from agentops.models import (
    API_BASE_URL,
    DEFAULT_MODEL_ID,
    DEV_MODEL_ID,
    ROLES,
    LiveModels,
    MissingApiKeyError,
    ModelSettings,
    ScriptedModels,
    model_factory,
    require_api_key,
)


class RecordingConstructor:
    """Stands in for ChatAnthropic and records each set of keyword arguments."""

    def __init__(self) -> None:
        self.built: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.built.append(kwargs)
        return kwargs


class Untouchable:
    """A scenario stand-in that counts every attribute read."""

    reads = 0

    def __getattribute__(self, name: str) -> Any:
        type(self).reads += 1
        return object.__getattribute__(self, name)


@pytest.fixture
def constructor(monkeypatch: pytest.MonkeyPatch) -> RecordingConstructor:
    recording = RecordingConstructor()
    monkeypatch.setattr(models, "chat_model_class", lambda: recording)
    return recording


@pytest.mark.parametrize(("dev", "expected_id"), [(False, DEFAULT_MODEL_ID), (True, DEV_MODEL_ID)])
def test_each_role_gets_its_own_client_on_the_fixed_api_address(
    constructor: RecordingConstructor, dev: bool, expected_id: str
) -> None:
    """E1: six clients, the expected ids, the key passed explicitly, the base URL fixed even
    with ANTHROPIC_BASE_URL in the environ, and the scenario never read."""
    environ = {"ANTHROPIC_BASE_URL": "https://evil.example"}
    live = LiveModels(ModelSettings.from_environment(dev=dev, environ=environ), SecretStr("k"))
    scenario = Untouchable()
    Untouchable.reads = 0

    live.graph_models(scenario)  # type: ignore[arg-type]
    live.baseline_model(scenario)  # type: ignore[arg-type]
    live.judge_model()

    assert Untouchable.reads == 0
    assert len(constructor.built) == len(ROLES)
    for kwargs in constructor.built:
        assert kwargs["model"] == expected_id
        assert kwargs["api_key"].get_secret_value() == "k"
        assert kwargs["base_url"] == API_BASE_URL == "https://api.anthropic.com"
        assert kwargs["timeout"] == 120
        assert kwargs["max_retries"] == 2


def test_one_role_can_be_overridden_and_unknown_roles_are_ignored() -> None:
    """E3."""
    environ = {"AGENTOPS_MODEL_RCA": "claude-x", "AGENTOPS_MODEL_FOO": "claude-y"}

    settings = ModelSettings.from_environment(dev=True, environ=environ)

    assert settings.model_ids["rca"] == "claude-x"
    assert {role: model for role, model in settings.model_ids.items() if role != "rca"} == {
        role: DEV_MODEL_ID for role in ROLES if role != "rca"
    }


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_key_is_missing_and_the_error_names_only_the_variable(blank: str) -> None:
    """E33."""
    with pytest.raises(MissingApiKeyError) as raised:
        require_api_key({"ANTHROPIC_API_KEY": blank})

    assert "ANTHROPIC_API_KEY" in str(raised.value)
    assert require_api_key({"ANTHROPIC_API_KEY": " k "}) == "k"


def test_no_repr_shows_the_key() -> None:
    """E33."""
    settings = ModelSettings.from_environment(dev=False, environ={})
    live = LiveModels(settings, SecretStr("fake-key-123"))

    assert "fake-key-123" not in repr(settings)
    assert "fake-key-123" not in repr(live)


def test_the_factory_needs_the_key_only_for_the_live_model() -> None:
    """R4: scripted needs no key; anthropic without one raises before any client exists."""
    assert isinstance(model_factory("scripted", dev=False, environ={}), ScriptedModels)
    with pytest.raises(MissingApiKeyError):
        model_factory("anthropic", dev=False, environ={})
    live = model_factory("anthropic", dev=True, environ={"ANTHROPIC_API_KEY": "k"})
    assert isinstance(live, LiveModels)
    assert set(live.model_ids().values()) == {DEV_MODEL_ID}


def test_the_scripted_factory_runs_no_judge_and_no_middleware() -> None:
    """E20 (factory half)."""
    scripted = ScriptedModels()

    assert scripted.runs_judge is False
    assert tuple(scripted.baseline_middleware()) == ()
