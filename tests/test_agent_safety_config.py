"""AgentShield findings of 2026-10-01: the agent-facing safety rules stay in place.

The scan flagged CLAUDE.md for missing indirect-injection and data-leakage defences. These tests
keep the fix from being removed quietly: the deny rules for secret files in `.claude/settings.json`,
and the CLAUDE.md section that tells agents to treat repository data as untrusted.
"""

import json
import re
from pathlib import Path

import pytest

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
    "Read(~/.ssh/**)",
    "Bash(sudo *)",
    "Bash(rm -rf *)",
]

# The five checks AgentShield 1.6.0 reported against CLAUDE.md, copied from its
# src/rules/prompt-defense.ts (ecc-agentshield@1.6.0, MIT). The scanner matches each pattern
# line by line, so a boundary that wraps across two lines is invisible to it.
AGENTSHIELD_1_6_0_CHECKS = {
    "role-escape": r"(?:do\s+not|never|must\s+not|cannot|don'?t|refuse|reject|ignore)\s+.{0,60}"
    r"(?:role|persona|character|identity|pretend|act\s+as|impersonat|role.?play)",
    "instruction-override": r"(?:do\s+not|never|must\s+not|cannot|don'?t|refuse|reject)\s+.{0,60}"
    r"(?:override|ignore|disregard|bypass|modify|change|alter)\s+.{0,40}"
    r"(?:instruction|system|rule|guideline|directive|prompt)",
    "data-leakage": r"(?:do\s+not|never|must\s+not|cannot|don'?t|refuse)\s+.{0,60}"
    r"(?:reveal|disclose|share|leak|expose|output|repeat|show)\s+.{0,40}"
    r"(?:system|prompt|instruction|internal|confidential|secret|private|api.?key|credential)",
    "indirect-injection": r"(?:(?:external|third.?party|user.?provided|untrusted|fetched|retrieved)"
    r"\s+.{0,30}(?:data|content|source|input|document|url|link|tool)\s+.{0,30}"
    r"(?:instruct|command|inject|malicious|trust)|indirect\s+.{0,10}(?:inject|prompt|attack))",
    "output-weaponization": r"(?:do\s+not|never|must\s+not|cannot|don'?t|refuse)\s+.{0,60}"
    r"(?:harm(?:ful)?|danger(?:ous)?|illegal|weapon|violen(?:t|ce)|exploit|malware|phishing"
    r"|attack(?:s|ing)?)",
}


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


@pytest.mark.parametrize("check_id", sorted(AGENTSHIELD_1_6_0_CHECKS))
def test_claude_md_states_each_boundary_on_one_line(check_id: str) -> None:
    pattern = re.compile(AGENTSHIELD_1_6_0_CHECKS[check_id], re.IGNORECASE)
    lines = CLAUDE_MD_PATH.read_text(encoding="utf-8").splitlines()
    assert any(pattern.search(line) for line in lines), (
        f"no single line of CLAUDE.md satisfies AgentShield's {check_id} check"
    )


def test_claude_md_names_the_injection_fixture_that_exists() -> None:
    text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
    assert INJECTION_SCENARIO.exists()
    assert INJECTION_SCENARIO.relative_to(REPO_ROOT).as_posix() in text
