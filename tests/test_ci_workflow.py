"""E30, E31, E33: the CI workflow, the pre-push hook and Dependabot (R16, R18); increment 3 E5:
the test-count floor (R5)."""

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT_PATH = REPO_ROOT / ".github" / "dependabot.yml"
CI_DOCUMENT_PATH = REPO_ROOT / "docs" / "ci.md"
PRE_PUSH_HOOK = ".githooks/pre-push"
LOWEST_ALLOWED_FLOOR = 240

GATES_IN_ORDER = [
    "python -m venv .venv",
    "ruff check",
    "ruff format --check",
    "pyright",
    "pytest",
    "--junitxml",
    "check_test_count.py",
    "pip_audit -r requirements.txt",
]
SHA_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d")
USES_LINE = re.compile(r"^\s*-?\s*uses:\s*(.+)$")


def load_workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def workflow_triggers(workflow: dict[Any, Any]) -> list[str]:
    # pyyaml reads the bare key `on` as the boolean True (YAML 1.1).
    # A list or a mapping of trigger names both yield the names when listed.
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    return list(triggers)


def the_only_job(workflow: dict[Any, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert len(jobs) == 1, f"expected one job, found {list(jobs)}"
    return next(iter(jobs.values()))


def gates_missing_or_out_of_order(run_text: str) -> list[str]:
    """Each gate is searched for only after the previous one, so a reordered gate is missing."""
    missing = []
    search_from = 0
    for gate in GATES_IN_ORDER:
        position = run_text.find(gate, search_from)
        if position == -1:
            missing.append(gate)
            continue
        search_from = position + len(gate)
    return missing


def test_workflow_runs_the_gates_in_order_with_a_read_only_token() -> None:
    workflow = load_workflow()

    job = the_only_job(workflow)
    steps = job["steps"]
    run_text = "\n".join(step["run"] for step in steps if "run" in step)
    checkout_steps = [
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    ]

    assert {"push", "pull_request"} <= set(workflow_triggers(workflow))
    assert workflow["permissions"] == {"contents": "read"}
    assert job["runs-on"] == "ubuntu-24.04"
    assert not str(job["runs-on"]).endswith("-latest")
    assert len(checkout_steps) == 1
    assert checkout_steps[0]["with"]["persist-credentials"] is False
    assert gates_missing_or_out_of_order(run_text) == []


def test_test_count_floor_is_at_least_240_and_docs_ci_states_the_same_number() -> None:
    workflow = load_workflow()
    floor_text = workflow["env"]["MIN_TESTS"]
    ci_document = CI_DOCUMENT_PATH.read_text(encoding="utf-8")

    floor = int(floor_text)

    assert floor >= LOWEST_ALLOWED_FLOOR
    assert floor % 10 == 0
    assert f"`MIN_TESTS` is {floor} as of increment 3" in ci_document


def test_actions_are_sha_pinned_and_no_secret_is_referenced() -> None:
    raw_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    uses_values = [
        match.group(1).strip()
        for match in (USES_LINE.match(line) for line in raw_text.splitlines())
        if match
    ]
    unpinned = [value for value in uses_values if not SHA_PINNED_ACTION.match(value)]

    assert uses_values, "the workflow uses no actions; the pin check would prove nothing"
    assert unpinned == []
    assert "secrets." not in raw_text
    assert "pull_request_target" not in raw_text


def test_pre_push_hook_is_executable_and_dependabot_covers_both_ecosystems() -> None:
    index_entry = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s", PRE_PUSH_HOOK],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    hook_text = (REPO_ROOT / PRE_PUSH_HOOK).read_text(encoding="utf-8")
    dependabot = yaml.safe_load(DEPENDABOT_PATH.read_text(encoding="utf-8"))
    schedule_by_ecosystem = {
        update["package-ecosystem"]: update["schedule"]["interval"]
        for update in dependabot["updates"]
    }

    assert index_entry.startswith("100755 "), f"hook index entry: {index_entry!r}"
    assert "ruff check" in hook_text
    assert "pytest" in hook_text
    assert hook_text.index("ruff check") < hook_text.index("pytest")
    assert schedule_by_ecosystem["pip"] == "weekly"
    assert schedule_by_ecosystem["github-actions"] == "weekly"
