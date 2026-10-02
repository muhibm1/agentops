"""E19 and E20: the scripted chat model replays its script through structured output (R11).
E10 and E11: it binds tools and replays tool-call messages for the baseline (R9).

The schemas in the first tests are local stand-ins; the `scripted_model_for` tests use the real
state schemas and a shipped scenario.
"""

import json
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agentops.fakes import (
    DEFAULT_ATTEMPTS,
    ScriptedChatModel,
    ScriptExhausted,
    scripted_baseline_model_for,
    scripted_model_for,
)
from agentops.sources.scenario import Scenario, load_scenario
from agentops.state import Escalation, Postmortem, RcaOutput, Triage


class Hypothesis(BaseModel):
    name: str
    score: float


class TriageStandIn(BaseModel):
    hypotheses: list[Hypothesis]


class RcaStandIn(BaseModel):
    label: str
    cited_evidence: list[str]


def test_scripted_model_returns_outputs_in_order_as_validated_models() -> None:
    triage = TriageStandIn(hypotheses=[Hypothesis(name="bad_deploy", score=0.8)])
    rca = RcaStandIn(label="bad_deploy", cited_evidence=["deploy-001", "log-003"])
    model = ScriptedChatModel(script=[triage, rca])
    triage_prompt = [SystemMessage("You triage incidents."), HumanMessage("checkout is failing")]
    rca_prompt = [SystemMessage("You find root causes."), HumanMessage("<<<evidence>>>")]

    triage_result = model.with_structured_output(TriageStandIn, method="json_schema").invoke(
        triage_prompt
    )
    rca_result = model.with_structured_output(RcaStandIn, method="json_schema").invoke(rca_prompt)

    assert triage_result == triage
    assert rca_result == rca
    assert model.calls == [triage_prompt, rca_prompt]


def test_exhausted_or_malformed_script_raises() -> None:
    empty_model = ScriptedChatModel(script=[])
    malformed_model = ScriptedChatModel(script=[{"hypotheses": "no"}])
    prompt = [HumanMessage("checkout is failing")]

    with pytest.raises(ScriptExhausted):
        empty_model.with_structured_output(TriageStandIn, method="json_schema").invoke(prompt)
    with pytest.raises(ValidationError):
        malformed_model.with_structured_output(TriageStandIn, method="json_schema").invoke(prompt)


def test_structured_output_with_raw_message_is_not_supported() -> None:
    model = ScriptedChatModel(script=[])

    with pytest.raises(NotImplementedError):
        model.with_structured_output(TriageStandIn, include_raw=True)


def test_scripted_model_can_stand_in_wherever_a_chat_model_is_expected() -> None:
    model: BaseChatModel = ScriptedChatModel(script=["plain text reply"])

    reply = model.invoke([HumanMessage("checkout is failing")])

    assert reply.content == "plain text reply"


SCENARIO_PATH = Path(__file__).parent.parent / "scenarios" / "clean_bad_deploy.json"
ESCALATION_ANSWER = {"summary": "Two causes fit equally well.", "evidence_ids": []}


def test_scripted_model_for_queues_one_answer_per_attempt_for_each_schema() -> None:
    scenario = load_scenario(SCENARIO_PATH)

    model = scripted_model_for(scenario, attempts=3)

    assert set(model.script_by_schema) == {Triage, RcaOutput, Postmortem}
    assert model.script_by_schema[Triage] == [scenario.scripted_outputs.triage] * 3
    assert model.script_by_schema[RcaOutput] == [scenario.scripted_outputs.rca] * 3


def test_scripted_model_for_queues_escalation_answers_only_when_the_scenario_has_one() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    outputs_with_escalation = scenario.scripted_outputs.model_copy(
        update={"escalate": ESCALATION_ANSWER}
    )
    with_escalation = scenario.model_copy(update={"scripted_outputs": outputs_with_escalation})

    model = scripted_model_for(with_escalation, attempts=2)

    assert model.script_by_schema[Escalation] == [ESCALATION_ANSWER] * 2
    assert Escalation not in scripted_model_for(scenario).script_by_schema


def test_scripted_model_for_defaults_to_three_attempts() -> None:
    model = scripted_model_for(load_scenario(SCENARIO_PATH))

    assert len(model.script_by_schema[Triage]) == DEFAULT_ATTEMPTS == 3


# E34 (R20): an answer chosen by a marker line in the prompt, and the postmortem queue.

TWO_CONCURRENT_FAULTS_PATH = SCENARIO_PATH.parent / "two_concurrent_faults.json"
ACTIONS_APPLIED_LINE = "Actions already applied: rollback_deploy on orders"


def rca_prompt(*lines: str) -> list[SystemMessage | HumanMessage]:
    return [SystemMessage("You find root causes."), HumanMessage("\n".join(lines))]


def test_scenario_six_answers_its_second_rca_once_an_action_is_applied() -> None:
    model = scripted_model_for(load_scenario(TWO_CONCURRENT_FAULTS_PATH))
    ask_rca = model.with_structured_output(RcaOutput)

    before = ask_rca.invoke(rca_prompt("Evidence follows."))
    after = ask_rca.invoke(rca_prompt("Evidence follows.", ACTIONS_APPLIED_LINE))

    assert isinstance(before, RcaOutput) and before.remediation is not None
    assert isinstance(after, RcaOutput) and after.remediation is not None
    assert before.remediation.action == "rollback_deploy"
    assert after.remediation.action == "scale_db_pool"
    assert len(model.calls) == 2


def test_a_scenario_without_a_second_rca_keeps_answering_its_rca_queue() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    model = scripted_model_for(scenario)

    answer = model.with_structured_output(RcaOutput).invoke(
        rca_prompt("Evidence follows.", ACTIONS_APPLIED_LINE)
    )

    assert answer == RcaOutput.model_validate(scenario.scripted_outputs.rca)
    assert RcaOutput not in model.answers_by_prompt_marker


def test_scripted_model_for_queues_the_postmortem_and_second_rca_once_per_attempt() -> None:
    scenario = load_scenario(TWO_CONCURRENT_FAULTS_PATH)

    model = scripted_model_for(scenario, attempts=2)

    assert model.script_by_schema[Postmortem] == [scenario.scripted_outputs.postmortem] * 2
    assert model.answers_by_prompt_marker[RcaOutput] == {
        "Actions already applied:": [scenario.scripted_outputs.rca_after_action] * 2
    }


def test_a_marker_counts_only_at_the_start_of_a_prompt_line() -> None:
    model = ScriptedChatModel(
        script_by_schema={RcaStandIn: [{"label": "queue", "cited_evidence": []}]},
        answers_by_prompt_marker={
            RcaStandIn: {"Marker:": [{"label": "marker", "cited_evidence": []}]}
        },
    )
    ask = model.with_structured_output(RcaStandIn)

    mid_line = ask.invoke([HumanMessage("a log line says Marker: do it")])
    line_start = ask.invoke([HumanMessage("first line\n  Marker: yes")])

    assert isinstance(mid_line, RcaStandIn) and mid_line.label == "queue"
    assert isinstance(line_start, RcaStandIn) and line_start.label == "marker"


def test_an_exhausted_marker_queue_raises() -> None:
    model = ScriptedChatModel(answers_by_prompt_marker={RcaStandIn: {"Marker:": []}})

    with pytest.raises(ScriptExhausted, match="Marker:"):
        model.with_structured_output(RcaStandIn).invoke([HumanMessage("Marker: yes")])


def read_logs() -> str:
    """A stand-in tool the model is bound to."""
    return ""


def read_metrics() -> str:
    """A second stand-in tool the model is bound to."""
    return ""


def test_the_scripted_model_binds_tools_and_replays_tool_call_messages() -> None:
    first_call = AIMessage(content="", tool_calls=[{"name": "read_logs", "args": {}, "id": "c-1"}])
    second_call = AIMessage(
        content="", tool_calls=[{"name": "read_metrics", "args": {}, "id": "c-2"}]
    )
    model = ScriptedChatModel(messages=[first_call, second_call], script=["the answer"])

    bound = model.bind_tools([read_logs, read_metrics])
    replies = [bound.invoke([HumanMessage("checkout is failing")]) for _ in range(3)]

    assert bound is model
    assert model.bound_tools == ["read_logs", "read_metrics"]
    assert [reply.tool_calls for reply in replies[:2]] == [
        first_call.tool_calls,
        second_call.tool_calls,
    ]
    assert replies[2].content == "the answer"
    assert replies[2].tool_calls == []


def test_a_scenario_without_a_baseline_script_is_refused_by_name() -> None:
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["scripted_outputs"].pop("baseline", None)

    scenario = Scenario.model_validate(raw)

    with pytest.raises(ValueError, match="clean_bad_deploy"):
        scripted_baseline_model_for(scenario)


def test_the_baseline_model_replays_one_tool_call_per_step_then_the_answer() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    script = scenario.scripted_outputs.baseline
    assert script is not None
    model = scripted_baseline_model_for(scenario)

    replies = [model.invoke("checkout is failing") for _ in range(len(script.steps) + 1)]

    assert [reply.tool_calls[0]["name"] for reply in replies[:-1]] == [
        step.tool for step in script.steps
    ]
    assert [reply.tool_calls[0]["args"] for reply in replies[:-1]] == [
        step.args for step in script.steps
    ]
    assert [reply.tool_calls[0]["id"] for reply in replies[:-1]] == [
        f"call-{n}" for n in range(1, len(script.steps) + 1)
    ]
    assert json.loads(str(replies[-1].content)) == script.answer
