# AgentOps

AgentOps is a LangGraph incident-response runtime. It investigates a simulated production
failure, proposes a remediation backed by cited evidence, stops for a human approval, and
executes only allowlisted actions, each one exactly once. The model recommends; the graph
enforces every edge, budget, retry and side effect in code.

Status: increment 2, the investigation half. The graph runs triage, parallel evidence collection
(logs, metrics, deploys), a merge step with an investigation budget, root-cause analysis and an
escalation path, over eight simulated scenarios with a scripted model, so no API key is needed:
`clean_bad_deploy`, `deploy_red_herring_db`, `dependency_timeout_noisy_neighbour`,
`late_partition_zero_rows`, `schema_drift_ingestion`, `two_concurrent_faults`,
`ambiguous_two_causes` and `log_injection_drop_table`. A run ends in one of two outcomes:
`awaiting_approval` (a root cause and a remediation, citing evidence) or `escalated` (a hand-off
summary and the reason: `budget_exhausted`, `ambiguous` or `no_evidence`). Both exit 0.
Approval, execution, the live model and the evaluation harness arrive in later increments.

To try it, follow the setup in [CONTRIBUTING.md](CONTRIBUTING.md), then run
`agentops run --scenario clean_bad_deploy --model scripted`. The command prints the triage
hypotheses, the loop count and remaining investigation budget, the evidence it collected, and
either the root cause with its remediation or the escalation reason and summary. Approval and
execution are not part of this increment, so the command changes nothing. Scenario files are
read from `--scenarios-dir`, which defaults to `scenarios` and is resolved relative to the
current directory, so run the command from the repository root or pass the directory.

The design, its decisions and the plan for each increment are in
[docs/superpowers/specs/2026-09-29-agentops-design.md](docs/superpowers/specs/2026-09-29-agentops-design.md).
How the code is laid out is in [docs/sdlc/codebase-map.md](docs/sdlc/codebase-map.md).
What CI checks, and the known gaps it accepts, are in [docs/ci.md](docs/ci.md). Licensed under
the [MIT License](LICENSE).
