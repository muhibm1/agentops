"""E19 and E20: the scripted chat model replays its script through structured output (R11).

The schemas in the first tests are local stand-ins; the `scripted_model_for` tests use the real
state schemas and a shipped scenario.
"""

from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agentops.fakes import DEFAULT_ATTEMPTS, ScriptedChatModel, ScriptExhausted, scripted_model_for
from agentops.sources.scenario import load_scenario
from agentops.state import Escalation, RcaOutput, Triage


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

    assert set(model.script_by_schema) == {Triage, RcaOutput}
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
