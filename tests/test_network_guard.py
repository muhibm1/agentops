"""E23, E24 and E34: every test runs offline and without the app key (R12, R17)."""

import os
import socket

import pytest
from conftest import NetworkBlockedInTests
from pydantic import SecretStr

from agentops.models import LiveModels, MissingApiKeyError, ModelSettings, model_factory


def test_a_public_host_and_a_public_address_are_refused() -> None:
    """E23."""
    with pytest.raises(NetworkBlockedInTests):
        socket.getaddrinfo("api.anthropic.com", 443)
    with socket.socket() as client, pytest.raises(NetworkBlockedInTests):
        client.connect(("203.0.113.1", 443))


def test_loopback_still_works() -> None:
    """E23: name lookup of localhost and a socketpair are allowed."""
    assert socket.getaddrinfo("localhost", 0)
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"x")
        assert right.recv(1) == b"x"


@pytest.mark.parametrize(
    "host", ["localhost.example.com", "127.0.0.1.example.com", "api.anthropic.com"]
)
def test_look_alike_hosts_are_refused(host: str) -> None:
    """E34."""
    with pytest.raises(NetworkBlockedInTests):
        socket.getaddrinfo(host, 443)


def test_connect_ex_is_guarded_too() -> None:
    """E34: refused for a public address, not for loopback."""
    with socket.socket() as client, pytest.raises(NetworkBlockedInTests):
        client.connect_ex(("203.0.113.1", 443))
    with socket.socket() as client:
        assert isinstance(client.connect_ex(("127.0.0.1", 9)), int)


@pytest.mark.live_smoke
def test_the_marker_alone_does_not_lift_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """E34: without the test key, a live_smoke test is still offline."""
    if os.environ.get("AGENTOPS_TEST_ANTHROPIC_API_KEY", "").strip():
        pytest.skip("The test key is set here, so the guard is meant to be lifted.")
    with pytest.raises(NetworkBlockedInTests):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_the_app_key_is_removed_from_every_test() -> None:
    """E24: a key exported in the shell never reaches a test body."""
    assert "ANTHROPIC_API_KEY" not in os.environ
    with pytest.raises(MissingApiKeyError):
        model_factory("anthropic", dev=False)
    with pytest.raises(MissingApiKeyError):
        LiveModels(ModelSettings.from_environment(dev=False, environ={}), SecretStr(""))
