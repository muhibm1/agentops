"""E3: the repository documents a contributor and an auditor need exist and say what they must;
increment 3 E21: they describe the five commands, the runtime files and the crash demo (R21)."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_NAME = "Muhammad Muhibullah"
FIVE_COMMANDS = (
    "agentops run",
    "agentops approve",
    "agentops resume",
    "agentops history",
    "agentops fork",
)
CRASH_DEMO_STEPS = (
    "agentops run --scenario clean_bad_deploy --model scripted --thread crash-demo",
    "agentops approve crash-demo --decision approve --crash-after-apply",
    "agentops resume crash-demo",
)
NEW_PACKAGE_PATHS = (
    "src/agentops/actions/",
    "approval.py",
    "execute.py",
    "verify.py",
    "postmortem.py",
    "runbooks/default.yaml",
    "agentops.sqlite",
    "audit/<incident_id>.jsonl",
    "audit/<incident_id>.ledger.jsonl",
)


def read_document(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def commands_section(profile_text: str) -> str:
    """The `commands:` block of the profile, comments included, up to the next top-level key."""
    section: list[str] = []
    is_inside = False
    for line in profile_text.splitlines():
        is_top_level_key = line[:1].isalpha()
        if is_top_level_key:
            is_inside = line.startswith("commands:")
        if is_inside:
            section.append(line)
    return "\n".join(section)


def test_licence_contributing_readme_ci_doc_and_profile_match_the_built_repo() -> None:
    licence = read_document("LICENSE")
    contributing = read_document("CONTRIBUTING.md")
    readme = read_document("README.md")
    ci_doc = read_document("docs/ci.md")
    profile_text = read_document(".workhorse/profile.yml")
    profile = yaml.safe_load(profile_text)

    assert licence.startswith("MIT License")
    assert OWNER_NAME in licence
    assert "git config core.hooksPath .githooks" in contributing
    assert "pip install -r requirements.txt -r requirements-dev.txt" in contributing
    assert "ruff check ." in contributing
    assert "pyright" in contributing
    assert "pytest -q" in contributing
    for reason in ("budget_exhausted", "ambiguous", "no_evidence"):
        assert reason in contributing
    assert readme.startswith("# AgentOps")
    for scenario_id in sorted(path.stem for path in (REPO_ROOT / "scenarios").glob("*.json")):
        assert scenario_id in readme
    assert "awaiting_approval" in readme
    assert "escalated" in readme
    claude_document = read_document("CLAUDE.md")
    assert "arrives in increment 2" not in claude_document
    assert "not wired yet" in claude_document
    assert "the owner performs it and no agent deploys or merges" in profile["notes"]
    assert "scripted_model_for" in read_document("docs/sdlc/codebase-map.md")
    assert "MIN_TESTS" in ci_doc
    assert "## Accepted advisories" in ci_doc
    assert "--check" in profile["commands"]["format"]
    assert any("src/agentops/fakes.py" in note for note in profile["conventions"]["style_notes"])
    assert not any("(believed)" in str(command) for command in profile["commands"].values())
    assert "(believed)" not in commands_section(profile_text)


def test_documents_describe_the_increment_3_commands_runtime_files_and_crash_demo() -> None:
    readme = read_document("README.md")
    claude_document = read_document("CLAUDE.md")
    contributing = read_document("CONTRIBUTING.md")
    ci_doc = read_document("docs/ci.md")
    codebase_map = read_document("docs/sdlc/codebase-map.md")
    ignored_patterns = read_document(".gitignore").splitlines()

    for command in FIVE_COMMANDS:
        assert command in readme
    for step in CRASH_DEMO_STEPS:
        assert step in readme
    assert "audit/<incident_id>.jsonl" in readme
    assert "agentops.sqlite" in readme
    assert "arrives in increment 3" not in claude_document
    assert "Status: increment 3" in readme
    assert "--crash-after-apply" in claude_document
    for scenario_field in ("postmortem", "rca_after_action", "rollback_deploy+scale_db_pool"):
        assert scenario_field in contributing
    assert "as of increment 3" in ci_doc
    for path in NEW_PACKAGE_PATHS:
        assert path in codebase_map
    assert "*.sqlite-*" in ignored_patterns
    assert ignored_patterns.index("*.sqlite-*") == ignored_patterns.index("*.sqlite") + 1
