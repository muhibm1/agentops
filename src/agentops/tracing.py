"""Whether LangSmith tracing is on, decided from the environment without importing LangSmith.

LangChain's tracer turns itself on from the same variables, so this module only reads them and
reports. The rule is stricter than the library's: tracing counts as on only when a switch is
truthy and `LANGSMITH_API_KEY` is set; a truthy switch without a key is a named problem.
"""

import os
from collections.abc import Mapping
from typing import NamedTuple

# The switch names LangSmith reads (confirmed against `langsmith.utils.tracing_is_enabled` and
# `get_env_var`, which try `TRACING_V2` then `TRACING` under the LANGSMITH_ and LANGCHAIN_
# prefixes). Any one of them truthy asks for tracing.
TRACING_SWITCHES = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
)
API_KEY_VARIABLE = "LANGSMITH_API_KEY"
PROJECT_VARIABLE = "LANGSMITH_PROJECT"
DEFAULT_PROJECT = "agentops"
# Compared after stripping and casefolding. A falsy value is treated as unset.
TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSY = frozenset({"", "0", "false", "no", "off"})


class TracingStatus(NamedTuple):
    enabled: bool
    # The LangSmith project the traces go to; None when tracing is off.
    project: str | None
    # Why the configuration cannot be used, naming the variables; None when it can.
    problem: str | None


TRACING_OFF = TracingStatus(enabled=False, project=None, problem=None)


def tracing_status(environ: Mapping[str, str] = os.environ) -> TracingStatus:
    """Read the tracing variables from `environ`. Never raises; a bad setting is a `problem`.

    Problems never quote a variable's value, because the value may be a secret pasted into the
    wrong variable.
    """
    unrecognised = [name for name in TRACING_SWITCHES if switch_value(environ, name) is None]
    if unrecognised:
        return TracingStatus(
            enabled=False,
            project=None,
            problem=(
                f"{unrecognised[0]} is neither on nor off. Set it to one of "
                f"{', '.join(sorted(TRUTHY))} to turn tracing on, or to one of "
                f"{', '.join(sorted(FALSY - {''}))} (or leave it unset) to keep it off."
            ),
        )
    switched_on = [name for name in TRACING_SWITCHES if switch_value(environ, name) is True]
    if not switched_on:
        return TRACING_OFF
    if not environ.get(API_KEY_VARIABLE, "").strip():
        return TracingStatus(
            enabled=False,
            project=None,
            problem=(
                f"{switched_on[0]} turns tracing on but {API_KEY_VARIABLE} is not set. "
                f"Set {API_KEY_VARIABLE}, or unset {switched_on[0]} to keep tracing off."
            ),
        )
    project = environ.get(PROJECT_VARIABLE, "").strip() or DEFAULT_PROJECT
    return TracingStatus(enabled=True, project=project, problem=None)


def switch_value(environ: Mapping[str, str], name: str) -> bool | None:
    """True for a truthy switch, False for a falsy or absent one, None for anything else."""
    value = environ.get(name, "").strip().casefold()
    if value in TRUTHY:
        return True
    if value in FALSY:
        return False
    return None
