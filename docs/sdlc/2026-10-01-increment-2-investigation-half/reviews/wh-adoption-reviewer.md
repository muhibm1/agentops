Verdict: 3 findings (0 critical, 0 high, 2 medium, 1 low)
Adoption score: 4

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | src/agentops/graph.py:31, src/agentops/nodes/evidence.py:5 (also merge.py, router.py, escalate.py ADR cites) | Bare "ADR 0001/0002/0003" is ambiguous: increment 1 (docs/sdlc/2026-10-01-increment-1-scaffold/adr/) and increment 2 (docs/sdlc/2026-10-01-increment-2-investigation-half/adr/) both number from 0001, and there is no index. fakes.py and CLAUDE.md cite increment 1's 0001 (scripted model); evidence.py cites increment 2's 0001 (retry in node). | Engineer searching "ADR 0001" for why reads retry inside the node opens the scaffold ADR about the scripted model and finds nothing. | Cite with the full ADR filename or the increment (e.g. "ADR inc2-0001"), or add docs/adr-index.md listing all six with paths and link it from CLAUDE.md and codebase-map.md. |
| medium | CONTRIBUTING.md:66 | "Adding a scenario" says scripted_outputs holds only `triage` and `rca`; this change adds an `escalate` answer for escalating scenarios (codebase-map.md says so). The diff did not touch CONTRIBUTING.md. | Contributor adds an ambiguous or no-evidence scenario without `escalate`; the run fails at the escalate node with a scripted-model error they cannot trace to the doc. | CONTRIBUTING.md:66: add `escalate` (required when expected_outcome is escalated) and name the escalation reasons. |
| low | docs/ci.md (MIN_TESTS line) | Floor of 80 is far below the new suite size (diff adds roughly 1,300 test lines across 8 new files); a deletion of many tests would still pass. | Silent loss of coverage. | Raise MIN_TESTS in .github/workflows/ci.yml to the real count and update docs/ci.md. |

What reads well (confirmed from diff): router.py, merge.py and escalate.py each open with a docstring giving purpose and decision table; README and CLAUDE.md status, demo output and exit codes match the new behaviour; codebase-map.md rows updated for every changed module; each module has a matching test file (test_router, test_merge, test_evidence_nodes, test_rca_escalate, test_scenario_runs).
Not covered: no runbook entry or observability notes for the escalation reasons (budget_exhausted, ambiguous, no_evidence); runbooks/ does not exist yet, listed as not present in codebase-map.md.

Findings outside scope: none
Not verified: ran `python -m pytest -q` in ~/AgentOps; it failed at collection (ModuleNotFoundError: agentops) because the package is not installed in this shell, so test results and the documented commands were not confirmed. Read via git diff and grep only: README.md, CLAUDE.md, docs/ci.md, CONTRIBUTING.md, codebase-map.md, module headers.

## Re-check (after fixer commits c08ff22..2d5813e)

| Finding | Status | Evidence |
|---|---|---|
| medium, ambiguous ADR cites | closed (confirmed) | `grep -rn "ADR [0-9]" src CLAUDE.md` excluding "increment-N ADR" returns nothing; graph.py, evidence.py, escalate.py now say "increment-1 ADR 0002" / "increment-2 ADR 0001" (9541864) |
| medium, CONTRIBUTING.md escalate answer | closed (confirmed) | CONTRIBUTING.md:65-73 now requires `escalate` (`summary`, `evidence_ids`) and names the three reasons; test_repo_documents.py asserts the reasons appear |
| low, MIN_TESTS floor | still open (confirmed) | `pytest --collect-only -q` collects 235 tests; ci.yml:12 `MIN_TESTS: "80"`, docs/ci.md:28 says 80. Deleting about 150 tests would still pass. Fix: raise to about 230 in ci.yml and docs/ci.md. |

New findings in fix-changed code: none. Not verified: tests were collected, not run.
Adoption score after re-check: 4 (low item remains; no blockers).
