"""E4 to E9, E32, E35 and E39: `ask_model` asks for json_schema with the raw reply, validates in
the node, records each call's tokens and model id, and sends a two-block system message whose
first block is stable and cached (R2, R5, R6; ADR 0002, ADR 0004)."""

import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.evals.outcome import RunOutcome
from agentops.fakes import ScriptedChatModel, message_text
from agentops.graph import MODEL_NODE_RETRY, build_graph, checkpoint_serializer
from agentops.nodes import (
    CACHE_CONTROL,
    CLOSING_MARKER_PATTERN,
    OPENING_MARKER_PATTERN,
    UNTRUSTED_DATA_RULE,
    ask_model,
    model_call_ledger,
    usage_action,
)
from agentops.nodes.escalate import ESCALATE_INSTRUCTIONS, hand_off_summary
from agentops.nodes.postmortem import POSTMORTEM_INSTRUCTIONS
from agentops.nodes.rca import RCA_INSTRUCTIONS
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import Action, Triage, new_incident

NONCE = re.compile(r"[0-9a-f]{16}")
GOOD_TRIAGE = {"hypotheses": [{"name": "recent_deploy_regression", "score": 0.9}]}


class RecordingModel(BaseChatModel):
    """Records the `with_structured_output` kwargs and answers with fixed raw replies."""

    replies: list[AIMessage]
    model: str = "claude-x"
    structured_kwargs: list[dict[str, Any]] = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages: list[BaseMessage], stop: Any = None, **kwargs: Any) -> Any:
        raise AssertionError("ask_model must go through with_structured_output")

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self.structured_kwargs.append(kwargs)

        def answer(_: Any) -> dict[str, Any]:
            raw = self.replies[min(self.calls, len(self.replies) - 1)]
            self.calls += 1
            return {"raw": raw, "parsed": None, "parsing_error": "unparsed"}

        return RunnableLambda(answer)


def reply(content: str, **usage: Any) -> AIMessage:
    message = AIMessage(content=content)
    if usage:
        message.usage_metadata = {"total_tokens": 0, **usage}  # type: ignore[typeddict-item]
    return message


def test_ask_model_asks_for_json_schema_with_the_raw_reply() -> None:
    """E4: the kwargs are json_schema and include_raw, never a forced tool choice."""
    import json

    model = RecordingModel(replies=[reply(json.dumps(GOOD_TRIAGE))], structured_kwargs=[])

    result, action = ask_model(model, Triage, "instructions", "human")

    assert model.structured_kwargs == [{"method": "json_schema", "include_raw": True}]
    assert result == Triage.model_validate(GOOD_TRIAGE)
    assert action.ok and action.tool == "model"


def test_a_reply_that_does_not_fit_raises_validation_error_with_its_tokens() -> None:
    """E5: a bad reply raises ValidationError, which the model-node retry policy retries."""
    model = RecordingModel(
        replies=[reply('{"hypotheses": "no"}', input_tokens=10, output_tokens=3)],
        structured_kwargs=[],
    )

    with pytest.raises(ValidationError) as raised:
        ask_model(model, Triage, "instructions", "human")

    assert ValidationError in MODEL_NODE_RETRY.retry_on  # type: ignore[operator]
    failed = raised.value.model_action  # type: ignore[attr-defined]
    assert failed.ok is False and failed.input_tokens == 10 and failed.model == "claude-x"


def system_blocks_of(call: list[BaseMessage]) -> list[dict[str, Any]]:
    system = call[0]
    assert isinstance(system, SystemMessage) and isinstance(system.content, list)
    return [block for block in system.content if isinstance(block, dict)]


@pytest.mark.parametrize("scenario", ["clean_bad_deploy"], indirect=True)
def test_triage_sends_one_cached_block_with_the_untrusted_data_rule(
    scenario: Scenario, scripted_model: ScriptedChatModel
) -> None:
    """E6: triage has no nonce, so its system message is one cached block."""
    from agentops.nodes.triage import triage

    incident = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)
    triage(incident, model=scripted_model)

    blocks = system_blocks_of(scripted_model.calls[0])
    assert len(blocks) == 1
    assert blocks[0]["text"] == TRIAGE_INSTRUCTIONS
    assert UNTRUSTED_DATA_RULE in blocks[0]["text"]
    assert blocks[0]["cache_control"] == CACHE_CONTROL


def test_fenced_nodes_keep_the_nonce_out_of_the_cached_block() -> None:
    """E7: block 1 is the same for every nonce and cached; block 2 carries the nonce."""
    from agentops.nodes import fence_rule, new_fence_nonce, system_blocks
    from agentops.nodes.escalate import escalate_stable_instructions
    from agentops.nodes.postmortem import postmortem_stable_instructions
    from agentops.nodes.rca import rca_stable_instructions

    for stable, instructions in [
        (rca_stable_instructions, RCA_INSTRUCTIONS),
        (postmortem_stable_instructions, POSTMORTEM_INSTRUCTIONS),
        (escalate_stable_instructions, ESCALATE_INSTRUCTIONS),
    ]:
        first, second = (system_blocks(stable(), fence_rule(new_fence_nonce())) for _ in range(2))
        assert first[0] == second[0]
        assert first[0]["text"].startswith(instructions)
        assert first[0]["text"].endswith(UNTRUSTED_DATA_RULE)
        assert first[0]["cache_control"] == CACHE_CONTROL
        assert not NONCE.search(first[0]["text"])
        assert NONCE.search(first[1]["text"]) and "cache_control" not in first[1]
        assert first[1] != second[1]


def test_usage_action_reads_tokens_cache_and_model_from_the_reply() -> None:
    """E8."""
    raw = reply(
        "{}",
        input_tokens=120,
        output_tokens=30,
        input_token_details={"cache_read": 50, "cache_creation": 20},
    )
    raw.response_metadata = {"model": "claude-x"}

    action = usage_action(raw, 1.0, "configured")

    assert action == Action(
        tool="model",
        ok=True,
        latency_ms=1.0,
        input_tokens=120,
        output_tokens=30,
        cache_read_tokens=50,
        cache_write_tokens=20,
        model="claude-x",
    )


def test_usage_action_sums_ttl_writes_and_falls_back_to_the_configured_id() -> None:
    """E9: per-lifetime write counts are summed; no usage and no id gives zeros and the
    configured id, never None."""
    ttl = reply(
        "{}",
        input_tokens=500,
        output_tokens=1,
        input_token_details={
            "cache_creation": 0,
            "ephemeral_5m_input_tokens": 300,
            "ephemeral_1h_input_tokens": 100,
        },
    )
    bare = AIMessage(content="{}")

    assert usage_action(ttl, 0.0, "claude-x").cache_write_tokens == 400
    empty = usage_action(bare, 0.0, "claude-x")
    assert (empty.input_tokens, empty.output_tokens, empty.cache_write_tokens) == (0, 0, 0)
    assert empty.model == "claude-x"


@pytest.mark.parametrize("scenario", ["clean_bad_deploy"], indirect=True)
def test_a_reply_that_never_fits_is_tried_three_times_and_every_try_is_priced(
    scenario: Scenario, source: SimulatorSource, tmp_path: Path
) -> None:
    """E32: triage retried by the graph three times; the ledger prices all three tries."""
    model = RecordingModel(
        replies=[reply('{"hypotheses": "no"}', input_tokens=7, output_tokens=2)],
        structured_kwargs=[],
    )
    graph = build_graph(
        source,
        model,
        default_registry(),
        AuditLog(tmp_path),
        InMemorySaver(serde=checkpoint_serializer()),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "e32"}, "recursion_limit": 60}
    incident = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)

    with model_call_ledger() as calls, pytest.raises((ValidationError, GraphRecursionError)):
        graph.invoke(incident, config)

    assert model.calls == 3
    assert len(calls) == 3
    assert all(not call.ok and call.input_tokens == 7 for call in calls)


@pytest.mark.parametrize("scenario", ["clean_bad_deploy"], indirect=True)
def test_an_in_node_retry_records_the_tokens_of_each_rejected_try(scenario: Scenario) -> None:
    """E32 for escalate: its own retries keep each rejected try's tokens in the state."""
    model = RecordingModel(
        replies=[reply("not json", input_tokens=5, output_tokens=1)], structured_kwargs=[]
    )
    state = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)

    _, actions = hand_off_summary(state, model)

    assert [action.attempt for action in actions] == [1, 2, 3]
    assert all(not action.ok and action.input_tokens == 5 for action in actions)
    assert all(action.detail == "ValidationError" for action in actions)


def test_records_saved_before_the_change_still_load() -> None:
    """E35: old Action and RunOutcome dicts load with defaults; an unknown key is refused."""
    old_action = {"tool": "model", "ok": True, "latency_ms": 1.0, "input_tokens": 3}
    loaded = Action.model_validate(old_action)
    assert (loaded.cache_read_tokens, loaded.cache_write_tokens, loaded.model) == (0, 0, None)
    with pytest.raises(ValidationError):
        Action.model_validate({**old_action, "surprise": 1})
    fields = RunOutcome.model_fields
    assert (
        fields["usage_by_model"].default_factory is not None
        or fields["usage_by_model"].default == {}
    )


INJECTED_PHRASE = "DROP TABLE"


@pytest.mark.parametrize("scenario", ["log_injection_drop_table"], indirect=True)
def test_the_injected_log_line_stays_between_the_fence_markers(
    scenario: Scenario, source: SimulatorSource, scripted_model: ScriptedChatModel, tmp_path: Path
) -> None:
    """E39: in the two-block format the payload appears only inside the human fence."""
    graph = build_graph(
        source,
        scripted_model,
        default_registry(),
        AuditLog(tmp_path),
        InMemorySaver(serde=checkpoint_serializer()),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "e39"}, "recursion_limit": 60}
    graph.invoke(
        new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time), config
    )

    fenced_calls = 0
    for call in scripted_model.calls:
        system, human = call
        system_text = message_text(system)
        assert system_text.strip(), "the fake must read text out of block lists"
        assert UNTRUSTED_DATA_RULE in system_blocks_of(call)[0]["text"]
        assert INJECTED_PHRASE.lower() not in system_text.lower()
        human_text = message_text(human)
        if INJECTED_PHRASE.lower() not in human_text.lower():
            continue
        fenced_calls += 1
        opening = OPENING_MARKER_PATTERN.search(human_text)
        closing = CLOSING_MARKER_PATTERN.search(human_text)
        assert opening and closing
        outside = human_text[: opening.start()] + human_text[closing.end() :]
        assert outside.strip()
        assert INJECTED_PHRASE.lower() not in outside.lower()
    assert fenced_calls > 0
