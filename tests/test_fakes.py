"""E19 and E20: the scripted chat model replays its script through structured output (R11).

The schemas here are local stand-ins so this file does not depend on `agentops.state`.
"""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agentops.fakes import ScriptedChatModel, ScriptExhausted


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
