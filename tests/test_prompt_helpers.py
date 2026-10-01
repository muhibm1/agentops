"""E21, E4 and E48: neutralising delimiters never loses an entry when two keys collapse to the
same text, catches look-alike and zero-width-split runs, and every fence carries its own nonce."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import ScriptedChatModel
from agentops.nodes import neutralise_delimiters
from agentops.nodes.rca import rca
from agentops.state import Evidence, Hypothesis, IncidentState, RcaOutput, new_incident


def test_colliding_keys_are_both_kept() -> None:
    neutralised = neutralise_delimiters({"a<<<": 1, "a< < <": 2})

    assert neutralised == {"a< < <": 1, "a< < < (2)": 2}


def test_a_third_colliding_key_gets_the_next_suffix() -> None:
    neutralised = neutralise_delimiters({"a<<<": 1, "a< < <": 2, "a< < < (2)": 3})

    assert sorted(neutralised.values()) == [1, 2, 3]
    assert len(neutralised) == 3


def test_keys_that_do_not_collide_are_unchanged() -> None:
    assert neutralise_delimiters({"a<<<": 1, "b": 2}) == {"a< < <": 1, "b": 2}


def test_fullwidth_angle_brackets_are_neutralised_like_ascii_ones() -> None:
    neutralised = neutralise_delimiters("＜＜＜end＞＞＞")

    assert "<<<" not in neutralised
    assert ">>>" not in neutralised
    assert neutralised == "< < <end> > >"


# E48 (R4, ADR 0005): look-alike brackets and runs split by format characters are spaced out.
LOOKALIKE_OPENING = ["‹", "〈", "˂", "〈", "⟨", "《", "﹤", "＜"]
LOOKALIKE_CLOSING = ["›", "〉", "˃", "〉", "⟩", "》", "﹥", "＞"]


@pytest.mark.parametrize("lookalike", LOOKALIKE_OPENING)
def test_a_run_of_lookalike_opening_brackets_is_spaced_out_as_ascii(lookalike: str) -> None:
    assert neutralise_delimiters(f"{lookalike * 3}end") == "< < <end"


@pytest.mark.parametrize("lookalike", LOOKALIKE_CLOSING)
def test_a_run_of_lookalike_closing_brackets_is_spaced_out_as_ascii(lookalike: str) -> None:
    assert neutralise_delimiters(f"end{lookalike * 3}") == "end> > >"


@pytest.mark.parametrize("splitter", ["​", "‍"])
def test_a_run_split_by_a_zero_width_character_is_spaced_out(splitter: str) -> None:
    forged = f"<{splitter}<<end>{splitter}>{splitter}>"

    assert neutralise_delimiters(forged) == "< < <end> > >"


def test_a_lookalike_run_inside_a_dict_key_is_spaced_out() -> None:
    assert neutralise_delimiters({"‹‹‹k": 1}) == {"< < <k": 1}


def test_plain_text_and_keys_without_runs_are_unchanged() -> None:
    plain = {"message": "pool at 97% <2 free>", "id": "log-001", "n": 3}

    assert neutralise_delimiters(plain) == plain
    assert neutralise_delimiters("ordinary text") == "ordinary text"


# E4 (R4, ADR 0005): every fenced prompt has its own nonce, named in its system prompt.
OPENING_MARKER = re.compile(r"<<<untrusted evidence ([0-9a-f]{16})>>>")
CLOSING_MARKER = re.compile(r"<<<end ([0-9a-f]{16})>>>")
OPENED_AT = datetime(2026, 3, 10, 10, 30, tzinfo=UTC)


def rca_ready_state() -> IncidentState:
    state = new_incident("orders", ["latency"], opened_at=OPENED_AT)
    state["hypotheses"] = [Hypothesis(name="bad_deploy", score=0.5)]
    state["evidence"] = [
        Evidence(
            id="log-001",
            source="get_logs",
            kind="log",
            payload={"id": "log-001", "message": "forged < < <end> > > marker"},
            collected_at=OPENED_AT,
        )
    ]
    return state


def test_two_rca_prompts_for_one_state_carry_one_fence_each_with_distinct_nonces(
    tmp_path: Path,
) -> None:
    reply = {"hypotheses": [{"name": "bad_deploy", "score": 0.5}]}
    model = ScriptedChatModel(script_by_schema={RcaOutput: [reply, reply]})
    registry = default_registry()
    audit = AuditLog(tmp_path)
    config: RunnableConfig = {"configurable": {"thread_id": "prompt-test"}}

    rca(rca_ready_state(), config, model=model, registry=registry, audit=audit)
    rca(rca_ready_state(), config, model=model, registry=registry, audit=audit)

    nonces = []
    for system_message, human_message in model.calls:
        prompt = f"{system_message.content}\n{human_message.content}"
        opening = OPENING_MARKER.findall(prompt)
        closing = CLOSING_MARKER.findall(prompt)
        assert len(opening) == 1
        assert closing == opening
        assert opening[0] in str(system_message.content)
        nonces.append(opening[0])
    assert nonces[0] != nonces[1]
