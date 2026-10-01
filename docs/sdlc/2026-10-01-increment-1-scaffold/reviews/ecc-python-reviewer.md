Verdict: 3 findings (0 critical, 0 high, 0 medium, 3 low)

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| low | src/agentops/cli.py:116-118 | Scripted model holds one triage entry, but triage is retried up to MODEL_NODE_RETRY.max_attempts (confirmed by reading; not run) | Scenario whose scripted triage fails schema validation -> retry finds an empty script -> run reports ScriptExhausted instead of the real ValidationError, hiding the cause | src/agentops/cli.py: use `[*[outputs.triage] * MODEL_NODE_RETRY.max_attempts, *[outputs.rca] * MODEL_NODE_RETRY.max_attempts]`, as already done for rca |
| low | scripts/check_test_count.py:18 | `int(floor)` and `ElementTree.parse` are unguarded (confirmed by reading) | CI passes a non-numeric floor or a missing junit file -> Python traceback, exit 1 with no sentence naming the cause (still fails closed, so only diagnosability suffers) | scripts/check_test_count.py: catch ValueError, OSError and ParseError in main and print one sentence to stderr, return 1 |
| low | src/agentops/graph.py:25 | SOURCE_NODE_RETRY retries on bare `Exception` (confirmed by reading) | A programming error such as a TypeError in collect_evidence is retried once before surfacing; harmless for read-only sources but doubles latency and log noise on bugs | src/agentops/graph.py: narrow retry_on to the source adapter's own error types once real adapters exist |

Findings outside scope: none
Not verified: ruff, mypy, bandit and pytest were not run; review was by reading the diff only. Tests under tests/ were not read.
Checked and clean: delimiter neutralisation in nodes/evidence.py (payload models forbid extra keys and have fixed field names, so only string values need it); scenario id path-traversal guard in cli.load_named_scenario (confirmed by reading the regex and listed-stem check).
