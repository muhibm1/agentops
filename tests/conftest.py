"""Shared fixtures: a scenario, its simulator source and its scripted model, with tracing off and
the network closed to everything but loopback."""

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentops.budget import VARIABLES as SPEND_VARIABLES
from agentops.fakes import ScriptedChatModel, scripted_model_for
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.tracing import TRACING_SWITCHES

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SCENARIO_IDS = list_scenario_ids(SCENARIOS_DIR)
TRACING_VARIABLES = [
    *TRACING_SWITCHES,
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_ENDPOINT",
]


# Hosts and addresses a test may still reach: Python's socketpair on Windows connects to
# loopback, and nothing else is needed offline.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
LIVE_SMOKE_MARKER = "live_smoke"
TEST_KEY_VARIABLE = "AGENTOPS_TEST_ANTHROPIC_API_KEY"


class NetworkBlockedInTests(RuntimeError):
    """A test tried to reach a host that is not loopback."""


@pytest.fixture(autouse=True)
def offline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test runs offline and without the app key, unless it is marked `live_smoke` AND the
    test-only key is set: the marker alone keeps the guard on (R12). A cap, ledger or replay
    mode exported in the shell is removed too, so no test spends or replays by accident."""
    if (
        request.node.get_closest_marker(LIVE_SMOKE_MARKER)
        and os.environ.get(TEST_KEY_VARIABLE, "").strip()
    ):
        yield
        return
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for variable in SPEND_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        refuse_unless_loopback(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        refuse_unless_loopback(address)
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        refuse_unless_loopback(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield


def refuse_unless_loopback(address: Any) -> None:
    """Raise `NetworkBlockedInTests` unless `address` (a host, or a tuple starting with one) is
    loopback. A Unix socket path is local and allowed."""
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode()
    if isinstance(address, (str, bytes)) and isinstance(host, str) and "/" in host:
        return
    if host not in LOOPBACK:
        raise NetworkBlockedInTests(f"Tests run offline; a connection to {host!r} was refused.")


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer shell with LangSmith tracing on must not send test prompts anywhere."""
    for name in TRACING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(params=SCENARIO_IDS)
def scenario(request: pytest.FixtureRequest) -> Scenario:
    """Every scenario in turn; narrow it with `parametrize("scenario", [...], indirect=True)`."""
    return load_scenario(SCENARIOS_DIR / f"{request.param}.json")


@pytest.fixture
def source(scenario: Scenario) -> SimulatorSource:
    return SimulatorSource(scenario)


@pytest.fixture
def scripted_model(scenario: Scenario) -> ScriptedChatModel:
    return scripted_model_for(scenario, attempts=3)
