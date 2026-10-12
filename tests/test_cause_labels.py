"""The shared root-cause catalogue (owner's decision, 2026-10-12): root-cause accuracy is an
exact match, so every scenario's ground truth and every scripted hypothesis name comes from one
list, and the triage and baseline prompts show that list to the model."""

import json
from pathlib import Path

from agentops.baseline import BASELINE_PROMPT
from agentops.evals.outcome import UNDETERMINED_LABEL
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.state import CAUSE_LABELS, HYPOTHESIS_NAME_MAX_LENGTH, SNAKE_CASE_NAME

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def scenario_documents() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCENARIOS_DIR.glob("*.json"))
    ]


def scripted_names(document: dict) -> list[str]:
    scripted = document["scripted_outputs"]
    names = []
    for key in ("triage", "rca", "rca_after_action"):
        names += [item["name"] for item in scripted.get(key, {}).get("hypotheses", [])]
    return names


def test_the_catalogue_is_distinct_snake_case_and_short() -> None:
    import re

    assert len(CAUSE_LABELS) == len(set(CAUSE_LABELS))
    assert UNDETERMINED_LABEL not in CAUSE_LABELS
    for label in CAUSE_LABELS:
        assert re.fullmatch(SNAKE_CASE_NAME, label)
        assert len(label) <= HYPOTHESIS_NAME_MAX_LENGTH


def test_every_ground_truth_and_scripted_label_is_in_the_catalogue() -> None:
    allowed = {*CAUSE_LABELS, UNDETERMINED_LABEL}
    for document in scenario_documents():
        assert document["ground_truth"]["root_cause"] in allowed, document["id"]
        for name in scripted_names(document):
            assert name in CAUSE_LABELS, (document["id"], name)
        for key in ("rca", "rca_after_action"):
            root_cause = (document["scripted_outputs"].get(key) or {}).get("root_cause")
            if root_cause:
                assert root_cause["label"] in CAUSE_LABELS, (document["id"], key)


def test_triage_and_the_baseline_are_shown_every_label() -> None:
    for label in CAUSE_LABELS:
        assert label in TRIAGE_INSTRUCTIONS
        assert label in BASELINE_PROMPT
