"""E3: the repository documents a contributor and an auditor need exist and say what they must."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_NAME = "Muhammad Muhibullah"


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
    assert readme.startswith("# AgentOps")
    for scenario_id in sorted(path.stem for path in (REPO_ROOT / "scenarios").glob("*.json")):
        assert scenario_id in readme
    assert "awaiting_approval" in readme
    assert "escalated" in readme
    assert "arrives in increment 2" not in read_document("CLAUDE.md")
    assert "scripted_model_for" in read_document("docs/sdlc/codebase-map.md")
    assert "MIN_TESTS" in ci_doc
    assert "80" in ci_doc
    assert "## Accepted advisories" in ci_doc
    assert "--check" in profile["commands"]["format"]
    assert any("src/agentops/fakes.py" in note for note in profile["conventions"]["style_notes"])
    assert not any("(believed)" in str(command) for command in profile["commands"].values())
    assert "(believed)" not in commands_section(profile_text)
