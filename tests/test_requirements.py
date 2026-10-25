"""E2: every requirement is an exact pin carrying its licence as a comment (R2).

A package not on PyPI is pinned to a full commit of its GitHub repository instead: as exact
as a version number, and it cannot move under the pin the way a branch or a tag can.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXACT_PIN_WITH_LICENCE = re.compile(r"^[A-Za-z0-9_.\[\]-]+==\d+(\.\d+)+\s+# \S.*$")
GITHUB_COMMIT_PIN_WITH_LICENCE = re.compile(
    r"^[A-Za-z0-9_.\[\]-]+ @ git\+https://github\.com/[\w.-]+/[\w.-]+@[0-9a-f]{40}\s+# \S.*$"
)
FLYWHEEL_COMMIT = "8271c46814e3724305588245da7920a893738859"
FLYWHEEL_PIN = (
    f"langsmith-flywheel @ git+https://github.com/muhibm1/flywheel@{FLYWHEEL_COMMIT}  # MIT"
)
EDITABLE_SELF_INSTALL = "-e ."


def is_exact_pin(line: str) -> bool:
    return bool(EXACT_PIN_WITH_LICENCE.match(line) or GITHUB_COMMIT_PIN_WITH_LICENCE.match(line))


def requirement_lines(file_name: str) -> list[str]:
    text = (REPO_ROOT / file_name).read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


@pytest.mark.parametrize("file_name", ["requirements.txt", "requirements-dev.txt"])
def test_every_requirement_is_an_exact_pin_with_a_licence(file_name: str) -> None:
    lines = requirement_lines(file_name)

    unpinned = [
        line
        for line in lines
        if not is_exact_pin(line)
        and not (file_name == "requirements-dev.txt" and line == EDITABLE_SELF_INSTALL)
    ]

    assert lines, f"{file_name} lists no requirements"
    assert unpinned == []


@pytest.mark.parametrize(
    "line",
    [
        "langsmith-flywheel @ git+https://github.com/muhibm1/flywheel@main  # MIT",
        "langsmith-flywheel @ git+https://github.com/muhibm1/flywheel@v0.1.0  # MIT",
        "langsmith-flywheel @ git+https://github.com/muhibm1/flywheel@8271c46  # MIT",
        "langsmith-flywheel @ git+https://github.com/muhibm1/flywheel  # MIT",
        f"langsmith-flywheel @ git+http://github.com/muhibm1/flywheel@{FLYWHEEL_COMMIT}  # MIT",
        f"langsmith-flywheel @ git+https://example.com/muhibm1/flywheel@{FLYWHEEL_COMMIT}  # MIT",
        f"langsmith-flywheel @ git+https://github.com/muhibm1/flywheel@{FLYWHEEL_COMMIT}",
        "langsmith-flywheel>=0.1  # MIT",
        "langchain==1.4.3",
    ],
)
def test_a_pin_that_can_move_or_names_no_licence_is_refused(line: str) -> None:
    """A branch, a tag, a short or missing commit, plain http, another host, a range or a
    missing licence is not an exact pin."""
    assert not is_exact_pin(line)


@pytest.mark.parametrize("line", ["langchain==1.4.3  # MIT", FLYWHEEL_PIN])
def test_a_version_or_a_full_github_commit_with_its_licence_is_an_exact_pin(line: str) -> None:
    assert is_exact_pin(line)


def test_runtime_requirements_pin_flywheel_to_one_commit() -> None:
    """The spend ladder (src/agentops/budget.py) comes from Flywheel, which is not on PyPI."""
    lines = requirement_lines("requirements.txt")

    assert FLYWHEEL_PIN in lines


def test_the_flywheel_gate_runs_the_commit_the_requirements_install() -> None:
    """The gate's Action installs its own Flywheel, so it must be the commit AgentOps imports."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "flywheel.yml").read_text(encoding="utf-8")

    assert f"uses: muhibm1/flywheel@{FLYWHEEL_COMMIT} " in workflow


def test_dev_requirements_install_the_package_itself() -> None:
    lines = requirement_lines("requirements-dev.txt")

    assert lines[-1] == EDITABLE_SELF_INSTALL


@pytest.mark.parametrize(
    "pin",
    [
        "langgraph-checkpoint-sqlite==3.1.1  # MIT",
        "aiosqlite==0.22.1  # MIT",
        "sqlite-vec==0.1.9  # MIT or Apache-2.0",
        "pyyaml==6.0.3  # MIT",
    ],
)
def test_runtime_requirements_pin_the_checkpointer_and_the_runbook_parser(pin: str) -> None:
    lines = requirement_lines("requirements.txt")

    assert pin in lines


def test_pyyaml_is_a_runtime_requirement_only() -> None:
    dev_lines = requirement_lines("requirements-dev.txt")

    assert not any(line.startswith("pyyaml==") for line in dev_lines)


def test_runtime_requirements_pin_langchain() -> None:
    lines = requirement_lines("requirements.txt")

    assert "langchain==1.4.3  # MIT" in lines


# E42 (R18): the dev server CLI is a development tool only, pinned with its licence.
def test_dev_requirements_pin_the_langgraph_cli() -> None:
    dev_lines = requirement_lines("requirements-dev.txt")
    runtime_lines = requirement_lines("requirements.txt")

    assert "langgraph-cli[inmem]==0.4.32  # MIT" in dev_lines
    assert not any(line.startswith("langgraph-cli") for line in runtime_lines)


def test_langsmith_is_a_runtime_requirement() -> None:
    lines = requirement_lines("requirements.txt")

    assert "langsmith==0.14.2  # MIT" in lines


@pytest.mark.parametrize(
    "pin",
    [
        "langgraph-api==0.15.1  # Elastic-2.0",
        "langgraph-runtime-inmem==0.35.1  # Elastic-2.0",
    ],
)
def test_the_dev_server_packages_are_pinned_with_their_licence(pin: str) -> None:
    dev_lines = requirement_lines("requirements-dev.txt")
    runtime_lines = requirement_lines("requirements.txt")

    assert pin in dev_lines
    assert pin not in runtime_lines


def test_runtime_requirements_pin_the_live_model_client() -> None:
    """E30: both live-client packages pinned exactly with their licence."""
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "langchain-anthropic==1.7.5  # MIT" in lines
    assert "anthropic==1.12.1  # MIT" in lines
