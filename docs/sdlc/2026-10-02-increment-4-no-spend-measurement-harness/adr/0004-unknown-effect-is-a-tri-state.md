# 0004: Record an apply that raised as a tri-state unknown effect that verify must judge

Date: 2026-10-02
Status: proposed
Change: 2026-10-02-increment-4-no-spend-measurement-harness

## Context

Both high findings of increment 3 came from `applied=False` meaning "nothing changed" when the
truth was "we do not know": a crash after apply, and a failed read declared cleared. The last
open low (ship.md D26, `executor.py` `apply_and_audit`) is the same shape: when `source.apply`
raises, the audit record says `applied: false` and the exception propagates, so the run fails
with exit 2 and state never records that an effect may exist. On resume the simulator's ledger
settles it, but a real source would be applied twice. The retro wrote the instinct
`unknown-effect-is-not-false`. R1 to R3 of this change.

## Decision

The executor catches the exception, writes an `apply` audit record with `applied: null`,
`effect: "unknown"`, `ok: false` and the exception's type name only, and returns the outcome
`apply_unknown` with `ExecutionRecord.applied = None`. Every `apply` record carries `effect`
(`applied`, `not_applied`, `unknown`). The execute node routes `apply_unknown` to `verify`; if
the symptoms cleared the run resolves as usual, otherwise it escalates with the new reason
`effect_unknown`, never loops and never reports `symptoms_persist`. The rca prompt lists such
actions under `Actions possibly applied:`, the postmortem says `(effect unknown)`, and the CLI
says the action may have been applied. Resume logic keeps testing `applied is True`, so an unknown
record never pretends to be a recovered apply; the ledger decides.

## Alternatives

| Option | Why not |
|--------|---------|
| Keep `applied: false` and add an `error` field | `false` already misled two reviewers' worth of code paths; a bool cannot say "unknown" |
| Re-raise as today and rely on the ledger at resume | Correct for the simulator only; a real source has no ledger the executor can read |
| Escalate at once without verifying | Throws away the one thing that can settle the question, the post-action signals |

## Consequences

Easier: every reader of `applied` must handle `None`, and pyright now points at each one.
Harder: `ExecutionRecord.applied` is `bool | None`, so three nodes and the CLI change in one
task; old checkpoints with a bool still load. The scripted model's rca marker is untouched (the
`possibly applied` line is a new line), so the eight scenarios need no new scripted output.
Revisit when a real adapter can report its own idempotent status after an error.
