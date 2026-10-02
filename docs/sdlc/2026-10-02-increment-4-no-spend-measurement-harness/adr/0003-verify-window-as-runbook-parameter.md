# 0003: Make the verify window a per-action runbook parameter anchored at the incident opening

Date: 2026-10-02
Status: proposed
Change: 2026-10-02-increment-4-no-spend-measurement-harness

## Context

Increment 3 fixed the verify window on the scenario side (post-action signals later than 30
minutes are rejected at load, `scenario.py` `late_signal_problems`) and left open the runtime
half: `VERIFY_WINDOW` in `sources/base.py` is a constant, so a real source whose effect arrives
later than 30 minutes after the incident opened would never be seen (ship.md D26, low). The
request asks for the window to become a runbook or scenario parameter. The simulator's clock is
scenario time (March 2026) while `ExecutionRecord.applied_at` is wall-clock now, so the window
cannot be anchored at the apply time without inventing a simulated clock. The runbook already
types what each action is and what must hold before it runs (`actions/runbook.py`), and
`preconditions.py` reads metrics with the same 30-minute lookahead.

## Decision

`ActionEntry` gains `verify_window_minutes` (integer, 1 to 1440, default 30, the default taken
from `VERIFY_WINDOW` so the two cannot disagree). `verify` reads the entry of the executed action
through the registry and reads signals from `opened_at` to `opened_at + window`; the metric
preconditions use the same lookahead. Scenario files keep the 30-minute rule at load time: a
scenario may not rely on a longer runbook window, so every shipped scenario works with every
runbook. The window stays anchored at the incident opening.

## Alternatives

| Option | Why not |
|--------|---------|
| A `verify_window_minutes` field on `Scenario`, copied into state | A new control field in `IncidentState`, a checkpoint shape change, and the window would describe the simulated world rather than the action whose effect is awaited |
| Anchor the window at `applied_at` | Impossible with the simulator's fixed scenario clock; would need a simulated clock in state |
| Leave the constant and document the limit | The finding stays open; a real adapter would need a code change for every slow action |

## Consequences

Easier: a slow action (a rollback that takes an hour to show) gets its own window in the file the
owner already reviews. Harder: `verify` depends on the registry, so `build_graph` passes it one
more argument, and the scenario validator and the runbook default must stay equal (a test asserts
it). Revisit when a real adapter exists: anchoring at the apply time may then be right.
