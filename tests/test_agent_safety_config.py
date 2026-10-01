"""AgentShield findings of 2026-10-01: the agent-facing safety rules stay in place.

The scan flagged CLAUDE.md for missing indirect-injection and data-leakage defences. These tests
keep the fix from being removed quietly: the the harness deny rules for secret files, and the
CLAUDE.md section that tells agents to treat repository data as untrusted.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
INJECTION_SCENARIO = REPO_ROOT / "scenarios" / "log_injection_drop_table.json"

REQUIRED_DENY_RULES = [
    "Read(./.env)",
    "Read(./.env.local)",
    "Read(./.env.*.local)",
    "Read(./.env.production)",
    "Read(./**/*.pem)",
    "Read(./**/*.key)",
]


def _deny_rules() -> list[str]:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return settings["permissions"]["deny"]


def test_harness_settings_deny_reading_secret_files() -> None:
    deny = _deny_rules()
    missing = [rule for rule in REQUIRED_DENY_RULES if rule not in deny]
    assert not missing, f"secret-file deny rules removed from .claude/settings.json: {missing}"


def test_harness_settings_allow_nothing() -> None:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    assert "allow" not in settings.get("permissions", {}), (
        "project settings must not grant permissions; they only restrict"
    )


def test_claude_md_keeps_the_safety_section() -> None:
    text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
    assert "## Working safely in this repository" in text
    assert "untrusted data" in text
    assert "Never read, print, paste or commit secrets" in text


def test_claude_md_names_the_injection_fixture_that_exists() -> None:
    text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
    assert INJECTION_SCENARIO.exists()
    assert INJECTION_SCENARIO.relative_to(REPO_ROOT).as_posix() in text
