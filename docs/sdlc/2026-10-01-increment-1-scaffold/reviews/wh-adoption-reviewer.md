Verdict: 3 findings (0 critical, 0 high, 2 medium, 1 low)
Adoption score: 4

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | spec.md:193 vs src/agentops/cli.py:121-134 | Spec "Observability" says "The CLI prints the counts" of `actions` (model and source calls, latency, success). `report()` prints only the evidence count; `state["actions"]` is never printed or logged. | On-call follows the spec to see which call failed or was slow, finds no output, and has no logging framework to fall back on. | Either print an action count and failure count in `report()` (with a test), or change spec.md Observability to say actions are only readable from the returned state. |
| medium | README.md:12-15, CONTRIBUTING.md:5-21 | Run section does not say that `agentops run` defaults `--scenarios-dir` to `scenarios` relative to the current directory. Running the demo from another directory exits 1 "scenarios directory ... does not exist". No pointer to `docs/sdlc/codebase-map.md` or a module list for finding code. | Engineer runs the documented demo from a subdirectory or after `pip install .` elsewhere and gets a usage error with no documented cause. | README.md: add "run from the repository root, or pass `--scenarios-dir`" and link `docs/sdlc/codebase-map.md` as the module map. |
| low | src/agentops/cli.py:109 vs src/agentops/fakes.py:88 | Two scripted-model builders with near-identical names: `scripted_model_for` (tests, one rca answer) and `scripted_model_with_retries` (CLI, rca repeated per retry). codebase-map.md:23 and ADR 0001 name only `scripted_model_for` as what the CLI uses. | Engineer changes `scripted_model_for` expecting the demo to change, or the reverse; a retry-count change in `MODEL_NODE_RETRY` affects only one. | Move `scripted_model_with_retries` next to `scripted_model_for` in fakes.py, or note in codebase-map.md and ADR 0001 that the CLI builds its own. |

Checked and fine (confirmed by reading): README links resolve to existing files (design doc, docs/ci.md, LICENSE, CONTRIBUTING.md); the six commands in CONTRIBUTING.md match CLAUDE.md; module docstrings exist on every new module and match spec vocabulary; three ADRs cover the non-obvious decisions (scripted model location, citation retry, CI shape); the pre-push hook matches the documented `.venv` layout.

Findings outside scope: none
Not verified: ran no commands (no install, pytest, ruff or pyright); documented commands were checked only by reading CONTRIBUTING.md, CLAUDE.md, the hook and cli.py. The repo has a `.venv` but I did not run `agentops --help`. No runbook exists in this increment; the spec scopes it out (runbook arrives later), so not raised as a finding.

## Re-check (fix commit db61178)

Adoption score: 4 (unchanged; no blockers below 4)

| Original finding | Status | Evidence (confirmed by reading the diff, codebase-map.md and tests/test_cli.py) |
|---|---|---|
| medium: spec says CLI prints action counts, `report()` did not | closed | `report()` now prints `actions: N (M failed)`; tests/test_cli.py:262 asserts it |
| medium: README silent on `--scenarios-dir` default and code map | closed | README.md:15-18 says run from the repository root or pass the directory; README.md:21 links docs/sdlc/codebase-map.md |
| low: `scripted_model_with_retries` (cli.py) vs `scripted_model_for` (fakes.py) undocumented | still open (low) | codebase-map.md:23 still names only `scripted_model_for`; ADR 0001 not touched by the fix. Fix: add one sentence to codebase-map.md:23 that the CLI builds its own queues in `cli.py` |

New finding in changed code: low, src/agentops/fakes.py:35-38. `ScriptedChatModel` now has two scripts (`script`, `script_by_schema`) and a schema-keyed path that bypasses `script`; the class doc was not extended. A schema present in `script_by_schema` never reads `script`. Fix: add that sentence to the class docstring.

Not verified: ran no commands; this re-check is by reading only.
