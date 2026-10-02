"""E20, F5, E48 and A4: tracing is on only with a truthy switch and a key, and no runtime module
imports a tracing or live-model client.

`tracing_status` is given a plain dict, so these tests never read or change the real environment.
"""

import ast
from pathlib import Path

import pytest

from agentops.tracing import DEFAULT_PROJECT, FALSY, TRUTHY, TracingStatus, tracing_status

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agentops"
FORBIDDEN_IMPORT_ROOTS = frozenset({"langsmith", "langchain_anthropic", "anthropic"})
KEY = {"LANGSMITH_API_KEY": "test-key-value"}


def test_tracing_is_on_only_when_both_variables_are_set() -> None:
    nothing = tracing_status({})
    langsmith = tracing_status({"LANGSMITH_TRACING": "true", **KEY})
    langchain = tracing_status({"LANGCHAIN_TRACING_V2": "1", **KEY})

    assert nothing == TracingStatus(enabled=False, project=None, problem=None)
    assert langsmith == TracingStatus(enabled=True, project="agentops", problem=None)
    assert langchain.enabled is True
    assert langchain.problem is None
    assert DEFAULT_PROJECT == "agentops"


def test_the_project_variable_names_the_project_when_tracing_is_on() -> None:
    named = tracing_status({"LANGSMITH_TRACING": "yes", "LANGSMITH_PROJECT": "lab", **KEY})
    blank = tracing_status({"LANGSMITH_TRACING": "on", "LANGSMITH_PROJECT": "  ", **KEY})
    off = tracing_status({"LANGSMITH_PROJECT": "lab", **KEY})

    assert named.project == "lab"
    assert blank.project == "agentops"
    assert off.project is None


def test_a_half_set_tracing_configuration_is_a_named_problem() -> None:
    switch_without_key = tracing_status({"LANGSMITH_TRACING": "true"})
    key_without_switch = tracing_status(KEY)

    assert switch_without_key.enabled is False
    assert switch_without_key.problem is not None
    assert "LANGSMITH_API_KEY" in switch_without_key.problem
    assert "LANGSMITH_TRACING" in switch_without_key.problem
    assert key_without_switch == TracingStatus(enabled=False, project=None, problem=None)


@pytest.mark.parametrize("blank_key", ["", "   ", "\t"])
def test_a_blank_key_counts_as_missing(blank_key: str) -> None:
    status = tracing_status({"LANGCHAIN_TRACING_V2": "true", "LANGSMITH_API_KEY": blank_key})

    assert status.enabled is False
    assert status.problem is not None
    assert "LANGSMITH_API_KEY" in status.problem
    assert "LANGCHAIN_TRACING_V2" in status.problem


def test_the_problem_never_quotes_a_variable_value() -> None:
    status = tracing_status({"LANGSMITH_TRACING": "sometimes", **KEY})

    assert status.problem is not None
    assert "test-key-value" not in status.problem
    assert "sometimes" not in status.problem


@pytest.mark.parametrize("falsy", ["false", "0", "False", "no", "off", "", "OFF", " 0 "])
def test_a_falsy_tracing_variable_counts_as_off_not_half_set(falsy: str) -> None:
    with_key = tracing_status({"LANGSMITH_TRACING": falsy, **KEY})
    without_key = tracing_status({"LANGSMITH_TRACING": falsy})

    assert with_key == TracingStatus(enabled=False, project=None, problem=None)
    assert without_key == TracingStatus(enabled=False, project=None, problem=None)


def test_the_falsy_cases_of_e48_for_the_langchain_variable_and_an_upper_case_true() -> None:
    langchain_off = tracing_status({"LANGCHAIN_TRACING_V2": "0", **KEY})
    upper_true = tracing_status({"LANGSMITH_TRACING": "TRUE", **KEY})

    assert langchain_off == TracingStatus(enabled=False, project=None, problem=None)
    assert upper_true.enabled is True


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "True", "YES", "On", " true "])
def test_every_truthy_spelling_turns_tracing_on_with_a_key(truthy: str) -> None:
    assert tracing_status({"LANGSMITH_TRACING": truthy, **KEY}).enabled is True


@pytest.mark.parametrize("unrecognised", ["maybe", "2", "enabled", "truee", "t", "y"])
def test_an_unrecognised_tracing_value_is_a_problem_not_silently_off(unrecognised: str) -> None:
    status = tracing_status({"LANGSMITH_TRACING": unrecognised, **KEY})

    assert status.enabled is False
    assert status.problem is not None
    assert "LANGSMITH_TRACING" in status.problem


def test_one_truthy_switch_wins_over_a_falsy_one() -> None:
    status = tracing_status({"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "true", **KEY})

    assert status.enabled is True


@pytest.mark.parametrize("switch", ["LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING"])
def test_the_other_switch_names_langsmith_reads_are_honoured(switch: str) -> None:
    on = tracing_status({switch: "true", **KEY})
    half = tracing_status({switch: "true"})

    assert on.enabled is True
    assert half.problem is not None
    assert switch in half.problem


def test_the_truthy_and_falsy_spellings_do_not_overlap() -> None:
    assert TRUTHY == frozenset({"1", "true", "yes", "on"})
    assert FALSY == frozenset({"", "0", "false", "no", "off"})
    assert not TRUTHY & FALSY


def test_no_runtime_module_imports_a_tracing_or_live_model_client() -> None:
    modules = sorted(SOURCE_ROOT.rglob("*.py"))

    offenders = {
        str(path.relative_to(SOURCE_ROOT)): sorted(import_roots(path) & FORBIDDEN_IMPORT_ROOTS)
        for path in modules
        if import_roots(path) & FORBIDDEN_IMPORT_ROOTS
    }

    assert len(modules) > 20
    assert offenders == {}


@pytest.mark.parametrize(
    "source",
    [
        "import langsmith",
        "import langsmith.run_helpers as helpers",
        "from langsmith import traceable",
        "from langchain_anthropic import ChatAnthropic",
        "import os, anthropic",
        "def f():\n    import anthropic\n",
        "try:\n    from langsmith.utils import tracing_is_enabled\nexcept ImportError:\n    pass\n",
    ],
)
def test_the_import_scan_finds_a_forbidden_import_in_any_form(source: str) -> None:
    roots = import_roots_of_text(source)

    assert roots & FORBIDDEN_IMPORT_ROOTS


@pytest.mark.parametrize(
    "source",
    [
        "import langchain_core",
        "from langchain_core.tracers import LangChainTracer",
        "from . import anthropic",
        "anthropic = 'a string'",
        "# import langsmith",
    ],
)
def test_the_import_scan_does_not_flag_look_alike_or_relative_imports(source: str) -> None:
    assert not import_roots_of_text(source) & FORBIDDEN_IMPORT_ROOTS


def import_roots(path: Path) -> set[str]:
    return import_roots_of_text(path.read_text(encoding="utf-8"))


def import_roots_of_text(source: str) -> set[str]:
    """The top-level package of every absolute import in `source`, at any depth of the tree."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots
