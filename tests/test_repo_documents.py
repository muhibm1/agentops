"""E3: the repository documents a contributor and an auditor need exist and say what they must."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_NAME = "Muhammad Muhibullah"


def read_document(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_licence_contributing_readme_ci_doc_and_profile_match_the_built_repo() -> None:
    licence = read_document("LICENSE")
    contributing = read_document("CONTRIBUTING.md")
    readme = read_document("README.md")
    ci_doc = read_document("docs/ci.md")

    assert licence.startswith("MIT License")
    assert OWNER_NAME in licence
    assert "git config core.hooksPath .githooks" in contributing
    assert "pip install -r requirements.txt -r requirements-dev.txt" in contributing
    assert "ruff check ." in contributing
    assert "pyright" in contributing
    assert "pytest -q" in contributing
    assert readme.startswith("# AgentOps")
    assert "## Accepted advisories" in ci_doc
