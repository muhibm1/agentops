"""Fixes for the three failures the first live run hit on 2026-10-12
(/mnt/project-files/agentops/live-eval-2026-10-12): the rca prompt asked for a snake_case label
that must also equal a free-text hypothesis name, the triage prompt never stated the name
length limit, and the baseline's final answer was only read when it was bare JSON."""

import pytest
from langchain_core.messages import AIMessage

from agentops.baseline import parse_answer
from agentops.nodes.rca import RCA_INSTRUCTIONS
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.state import HYPOTHESIS_NAME_MAX_LENGTH

ANSWER = '{"root_cause": "late_upstream_partition", "action": null, "target": null, "params": {}}'


def test_triage_asks_for_snake_case_names_within_the_length_limit() -> None:
    assert "snake_case" in TRIAGE_INSTRUCTIONS
    assert f"at most {HYPOTHESIS_NAME_MAX_LENGTH} characters" in TRIAGE_INSTRUCTIONS


def test_rca_asks_for_the_label_copied_exactly_from_the_top_hypothesis() -> None:
    assert "snake_case" not in RCA_INSTRUCTIONS
    assert "copied exactly" in RCA_INSTRUCTIONS


@pytest.mark.parametrize(
    "text",
    [
        ANSWER,
        f"```json\n{ANSWER}\n```",
        f"The partition landed late, so I re-ran it.\n\n{ANSWER}",
        f"Here is my answer:\n```json\n{ANSWER}\n```\nLet me know if you need more.",
    ],
)
def test_the_baseline_answer_is_read_from_prose_or_a_fenced_block(text: str) -> None:
    answer, failure = parse_answer(AIMessage(content=text))

    assert failure is None
    assert answer is not None
    assert answer.root_cause == "late_upstream_partition"


def test_the_baseline_answer_is_read_from_content_blocks() -> None:
    message = AIMessage(content=[{"type": "text", "text": f"Done.\n{ANSWER}"}])

    answer, failure = parse_answer(message)

    assert failure is None
    assert answer is not None


@pytest.mark.parametrize(
    ("text", "expected_failure"),
    [
        ("I could not find the cause.", "JSONDecodeError"),
        ('{"action": "rollback_deploy"}', "ValidationError"),
        ('{"root_cause": ', "JSONDecodeError"),
    ],
)
def test_a_reply_with_no_readable_answer_still_fails_naming_why(
    text: str, expected_failure: str
) -> None:
    answer, failure = parse_answer(AIMessage(content=text))

    assert answer is None
    assert failure == expected_failure


def test_the_last_readable_object_is_the_answer() -> None:
    text = (
        'I first thought {"root_cause": "recent_deploy_regression"} but the logs disagree.\n'
        f"{ANSWER}"
    )

    answer, _ = parse_answer(AIMessage(content=text))

    assert answer is not None
    assert answer.root_cause == "late_upstream_partition"
