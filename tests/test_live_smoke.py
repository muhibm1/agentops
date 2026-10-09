"""E26 and E27: the one test that calls the live model, and the gate that keeps it off unless
the test-only key is set (R13). The key comes from a separate Anthropic workspace with its own
spend limit (docs/hosted-config.md); `ANTHROPIC_API_KEY` is never read as a fallback."""

import os
from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from agentops.models import DEV_MODEL_ID, LiveModels, ModelSettings
from agentops.nodes import ask_model
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.state import Triage

TEST_KEY_VARIABLE = "AGENTOPS_TEST_ANTHROPIC_API_KEY"
SMOKE_PROMPT = (
    "Service: checkout\nSymptoms:\n- HTTP 500 rate rose from 0.1% to 12% at 10:02 UTC\n"
    "- p99 latency doubled after the 10:00 UTC deploy"
)


def live_smoke_key(environ: Mapping[str, str]) -> str | None:
    """The stripped test-only key, or None when it is unset or blank. Never the app key."""
    key = environ.get(TEST_KEY_VARIABLE, "").strip()
    return key or None


def test_the_gate_reads_only_the_test_variable() -> None:
    """E26."""
    assert live_smoke_key({"ANTHROPIC_API_KEY": "live-key"}) is None
    assert live_smoke_key({}) is None
    assert live_smoke_key({TEST_KEY_VARIABLE: "  "}) is None
    assert live_smoke_key({TEST_KEY_VARIABLE: "t"}) == "t"


@pytest.mark.live_smoke
def test_one_live_triage_call_on_the_dev_model() -> None:
    """E27: one triage call on Haiku returns a Triage with real usage. Costs well under a cent
    (believed); runs only with the test-only key set."""
    key = live_smoke_key(os.environ)
    if key is None:
        pytest.skip(f"{TEST_KEY_VARIABLE} is not set; the live smoke test runs only with it.")
    live = LiveModels(ModelSettings.from_environment(dev=True, environ={}), SecretStr(key))

    result, action = ask_model(live.client("triage"), Triage, TRIAGE_INSTRUCTIONS, SMOKE_PROMPT)

    assert isinstance(result, Triage) and result.hypotheses
    assert action.input_tokens > 0
    assert action.model is not None and action.model.startswith("claude-haiku")
    assert live.settings.model_ids["triage"] == DEV_MODEL_ID
