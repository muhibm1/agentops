# 0003: CI mirrors the profile's venv install and asserts a test-count floor

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-1-scaffold

## Context

The profile commands, `CONTRIBUTING.md` and the pyright configuration all name `.venv`
(constraints T8). Design §13 asks for one credential-free CI job that asserts a test-count floor
from the JUnit report, SHA-pinned actions, and a test that parses the workflow (R16, R17). The
global standard says a green check that executed zero tests is worse than no check.

## Decision

The workflow creates `.venv` with `python -m venv .venv` and runs every step through
`.venv/bin/python -m <tool>`, the Linux twin of the profile's `.venv/Scripts/python.exe`.
`pytest` writes `junit.xml`; `scripts/check_test_count.py junit.xml "$MIN_TESTS"` fails the job
when passed tests (tests minus skipped, failures and errors) are below 30. The two actions are
pinned by commit SHA with the tag in a trailing comment; `tests/test_ci_workflow.py` parses the
file and asserts the pin format, the absence of `secrets.`, `permissions: contents: read`, the
step order and the floor step. Dependabot (`pip`, `github-actions`, weekly) moves the pins.

## Alternatives

| Option | Why not |
|--------|---------|
| Runner Python, no venv | pyright's `venv` setting and the contributing doc would describe a layout CI does not use |
| `pytest` plugin for the floor | A ten-line script with a unit test is easier to read than a plugin and runs anywhere |
| Floor of 1 (non-zero) | Would pass with one surviving test after a bad merge; 30 is below the planned count and above any accidental subset |
| Tag-pinned actions (`@v7`) | A moved tag changes what runs; the design and the global standard require the SHA |

## Consequences

Easier: a human and CI run the same commands; a pin or secret regression is a test failure
before it is a review comment. Harder: the floor must be raised deliberately as the suite grows
(documented in `docs/ci.md`), and `pip-audit` can turn CI red for a transitive advisory with no
fix, which is handled by an `--ignore-vuln` entry recorded in `docs/ci.md`. The build check
(`langgraph build`) is deferred with `langgraph.json` to increment 3 (brief D7).
