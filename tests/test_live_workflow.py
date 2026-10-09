"""E28: the live smoke workflow runs only by hand, reads only the test-only secret, and is
pinned like ci.yml; ci.yml still reads no secret (R14)."""

import re
from pathlib import Path
from typing import Any

import yaml
from test_ci_workflow import SHA_PINNED_ACTION, USES_LINE

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
LIVE_PATH = WORKFLOWS / "live-smoke.yml"
CI_PATH = WORKFLOWS / "ci.yml"
SECRET_REFERENCE = re.compile(r"secrets\.([A-Za-z0-9_]+)")


def triggers(workflow: dict[Any, Any]) -> Any:
    # pyyaml reads the bare key `on` as the boolean True (YAML 1.1).
    return workflow.get("on", workflow.get(True))


def test_the_live_job_runs_by_hand_only_with_a_read_only_token() -> None:
    workflow = yaml.safe_load(LIVE_PATH.read_text(encoding="utf-8"))

    assert triggers(workflow) == ["workflow_dispatch"]
    assert workflow["permissions"] == {"contents": "read"}


def test_the_live_job_reads_only_test_secrets_and_pins_its_actions() -> None:
    text = LIVE_PATH.read_text(encoding="utf-8")

    secrets = SECRET_REFERENCE.findall(text)
    assert secrets and all(name.startswith("AGENTOPS_TEST_") for name in secrets)
    assert "secrets.ANTHROPIC_API_KEY" not in text
    uses = [match.group(1).strip() for match in map(USES_LINE.match, text.splitlines()) if match]
    assert uses and all(SHA_PINNED_ACTION.match(value) for value in uses)


def test_the_live_job_checks_the_key_runs_the_smoke_test_and_counts_it_in_order() -> None:
    workflow = yaml.safe_load(LIVE_PATH.read_text(encoding="utf-8"))
    (job,) = workflow["jobs"].values()
    runs = "\n".join(step.get("run", "") for step in job["steps"])

    key_check = runs.index("AGENTOPS_TEST_ANTHROPIC_API_KEY is not set")
    smoke = runs.index("-m live_smoke")
    count = runs.index("check_test_count.py live.xml 1")
    assert key_check < smoke < count
    assert "Settings" in runs and "--junitxml" in runs


def test_ci_still_has_one_job_and_reads_no_secret() -> None:
    text = CI_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    assert len(workflow["jobs"]) == 1
    assert "secrets." not in text
