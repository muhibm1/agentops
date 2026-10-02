# 0001: Put the harness code in `src/agentops/evals/` and the pinned data in `evals/`

Date: 2026-10-02
Status: proposed
Change: 2026-10-02-increment-4-no-spend-measurement-harness

## Context

Design section 13 lays out `evals/dataset.py evaluators.py run.py checksums` at the repository
root and `baseline.py` under `src/agentops/`. The package is installed from `src/` only
(`pyproject.toml`, `[tool.setuptools.packages.find] where = ["src"]`, confirmed), so a module at
`evals/run.py` is importable only when the current directory is the repository root. R13 wants
`agentops eval` as a command of the installed CLI, and R10 wants the evaluators importable by a
later `langsmith.evaluate` call. Pyright's `include` lists `src`, `tests` and `scripts`; adding
`evals` means editing `pyproject.toml`, an ask-first path. The profile's tier floor and the
sensitive globs name `evals/**`, `evals/checksums*` and `**/held_out/**` for the data that decides
whether published results are honest.

## Decision

The harness code lives in a package `src/agentops/evals/` (`outcome.py`, `evaluators.py`,
`judge.py`, `dataset.py`, `run.py`, `commands.py`), importable as `agentops.evals` wherever the
package is installed. The repository-root `evals/` directory holds only data: `held_out/manifest.json`
and `checksums.sha256`. `baseline.py` stays at `src/agentops/baseline.py` as designed. The
codebase map records the deviation from design section 13.

## Alternatives

| Option | Why not |
|--------|---------|
| `evals/*.py` at the root, as designed | Not importable from the installed command; needs a pyright root and a `sys.path` trick in the CLI |
| A second installable package `evals/` | Two packages for one small project; the tier floor and globs would need rewriting |
| Everything under `src/agentops/evals/`, data included | The sensitive globs `evals/checksums*` and `**/held_out/**` would still match, but reviewers expect the pinned data at the root, where the design and the profile point |

## Consequences

Easier: one import root, one pyright configuration, `agentops eval` works from any directory with
`--scenarios-dir`. Harder: the design document and the codebase map disagree until the map is
updated in this change (task 10). The tier-2 floor on `evals/**` now covers only data; the code
is covered by the change's tier 3 anyway. Revisit if a second harness consumer (the console
repository) needs the evaluators as a separate distribution.
