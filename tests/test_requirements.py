"""E2: every requirement is an exact pin carrying its licence as a comment (R2)."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXACT_PIN_WITH_LICENCE = re.compile(r"^[A-Za-z0-9_.\[\]-]+==\d+(\.\d+)+\s+# \S.*$")
EDITABLE_SELF_INSTALL = "-e ."


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
        if not EXACT_PIN_WITH_LICENCE.match(line)
        and not (file_name == "requirements-dev.txt" and line == EDITABLE_SELF_INSTALL)
    ]

    assert lines, f"{file_name} lists no requirements"
    assert unpinned == []


def test_dev_requirements_install_the_package_itself() -> None:
    lines = requirement_lines("requirements-dev.txt")

    assert lines[-1] == EDITABLE_SELF_INSTALL
