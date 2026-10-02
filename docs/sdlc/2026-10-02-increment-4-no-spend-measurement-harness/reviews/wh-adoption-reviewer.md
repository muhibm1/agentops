Verdict: 3 findings (0 critical, 0 high, 1 medium, 2 low)
Adoption score: 4

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | README.md (section "When it fails") | No entries for the new failure modes: `agentops eval`/`dataset` exit 1 on a checksum mismatch, exit 1 on a tracing problem, exit 2 on an unreadable dataset file. Exit codes are only described in "Measuring it" and "The frozen split" | Engineer sees "checksum mismatch" after editing a held-out scenario, searches "When it fails", finds nothing, and may run `dataset --write --force` without the ask-first step | README.md: add three bullets to "When it fails" (checksum mismatch -> revert the file or follow the CONTRIBUTING.md held-out rule; tracing problem -> set both switch and `LANGSMITH_API_KEY` or unset; exit 2 -> which file) |
| low | README.md:23 | "The five commands" is stale next to CONTRIBUTING.md "six commands"; the quick list does not show `agentops eval --model scripted` or `agentops dataset` | Engineer follows the quick list and never finds the eval command until section "Measuring it" | README.md: fix the count and add one line pointing to "Measuring it" |
| low | README.md / CONTRIBUTING.md | The five new ADRs (harness location, frozen split, verify window, tri-state unknown effect, baseline scripts) live only under `docs/sdlc/<id>/adr/`; neither doc links them | Engineer wonders why baseline scripts sit in scenario files or why `effect: "unknown"` exists and cannot find the reason | README.md or CONTRIBUTING.md: link `docs/sdlc/2026-10-02-increment-4-no-spend-measurement-harness/adr/` |

Findings outside scope: none
Not verified: Ran (confirmed, .venv python) `python -m agentops eval --help`, `dataset --help`, `dataset` (exit 0, checksums match) and `eval --model scripted --system both` (table and header sentence match README, graph fails two_concurrent_faults root_cause_accuracy as documented). Did not run `langgraph dev` (needs browser/network and sends analytics), did not run the test suite, did not read src beyond module docstrings.
Confirmed: README, CONTRIBUTING (baseline script section), docs/ci.md (MIN_TESTS 700, no eval/langgraph step, unaudited CLI) and codebase-map were all updated in this diff, and module docstrings in evals/commands.py, tracing.py, dev_server.py state purpose and exit codes.

## Re-check

Adoption score: 5 (was 4). Verdict: 0 blockers; 1 new low.

| Original finding | Status | Evidence (confirmed by reading `git diff 78bc4a4..HEAD`) |
|---|---|---|
| medium: "When it fails" lacked eval/dataset entries | closed | README.md adds three bullets: checksum mismatch (exit 1), tracing problem (exit 1), unreadable file (exit 2), each with a next step. Commit 2514723 |
| low: "five commands" stale | closed | README.md now says "five thread commands" and points to "Measuring it" for `eval` and `dataset` |
| low: ADRs not linked | closed | CONTRIBUTING.md adds "Design decisions" linking the increment 4 `adr/` directory (five ADRs present) |

New text checked: the tracing paragraph now states which commands enforce the switch rule, and the `langgraph dev` text names the Elastic-2.0 licence of the two new pins. Both are accurate against the diff and clear.

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| low | README.md ("When it fails", checksum bullet) | Suggests `dataset --write --force` when a change is intended, but does not say the held-out split is ask-first (CLAUDE.md "Protected") | Engineer regenerates checksums after editing a held-out scenario without asking the owner | README.md: add "held-out files need the owner's approval first; see CONTRIBUTING.md" |

Not verified: did not re-run the CLI or tests; this re-check read the diff only.
