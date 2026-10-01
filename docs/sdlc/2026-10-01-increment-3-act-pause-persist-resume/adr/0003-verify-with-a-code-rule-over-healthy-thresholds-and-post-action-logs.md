# 0003: Decide "symptoms cleared" with a code rule over healthy metric thresholds and post-action logs

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-3-act-pause-persist-resume

## Context

Design section 4 has `verify` re-read the source after an action and route to `postmortem`
when the symptoms are cleared, otherwise back to the router (R15). Symptoms are free text, and
no model may decide a control edge (profile style note 1). The eight scenarios' post-action
worlds (confirmed by reading `scenarios/*.json`) differ in shape: scenario 4 has no metrics at
all and recovers only in its logs; scenario 5's `rows_written` starts at zero, so "back to
baseline" is the wrong test; scenario 6 clears half its metrics after the first action.

## Decision

`verify` reads the metric catalogue and the logs for the window from the incident's opening to
thirty minutes after it, keeps only points and lines later than the incident opened, and applies
one rule written in code: symptoms are cleared when at least one post-action signal exists,
every observed catalogue metric's latest post-action value is inside its healthy range
(`error_rate` at most 0.01, `p99_latency_ms` at most 500, `db_pool_utilisation`,
`disk_io_utilisation` and `memory_utilisation` at most 0.85, `dependency_p99_ms` at most 1000,
`ingestion_lag_seconds` at most 3600, `rows_written` at least 1), and no post-action log line is
at level ERROR. The table lives beside the catalogue the evidence nodes already hard-code.
Scenario 6 gains a combined post-action world keyed `rollback_deploy+scale_db_pool` so the second
action can clear what the first left.

## Alternatives

| Option | Why not |
|--------|---------|
| Compare each metric with its first pre-incident point | Scenario 5 starts in the bad state, so zero rows would count as healthy |
| Let the scenario file declare the clearing rule | The grading key must never reach graph code through anything but the `EvidenceSource` |
| Ask the model whether the symptoms are gone | A control edge decided by a model output, which the design forbids |

## Consequences

Easier: the rule is deterministic, testable per scenario, and explainable in a postmortem.
Harder: the thresholds are opinions about synthetic services; a new scenario must fit them or
extend the table in the same change. Revisit when parameterised scenarios arrive in week two.
