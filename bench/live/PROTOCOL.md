# Live shop protocol (fixed before any test fault was injected)

**What is tested.** AgentOps' graph, unchanged, runs against the OpenTelemetry demo shop, a
microservice web shop with its own stock observability (Prometheus span metrics and container
stats, OpenSearch logs) that AgentOps did not build. A fault is switched on through the shop's
own feature-flag service (flagd). The on-call is paged with one sentence: customers report pages
failing or slow. AgentOps must find the faulty service, propose a fix, wait for approval, apply
it, and check the shop recovered.

**What the agent can do.** Exactly one action, `turn_off_service_feature_flags` on a named shop
service (`bench/live/runbook.yaml`), behind the graph's approval pause and the runbook's
precondition that the named service is unhealthy. The agent is never told which flags exist or
which are on; acting on the wrong service changes nothing.

**What it reads.** `bench/live/source.py`: per-service health ranked by the frozen RCAEval
statistics (no retuning: same spread, score and weights; only the row interval differs), new
WARN-or-worse log lines from the shop's services, and the paged service's error ratio and p99
latency. After a change, reads wait 150 s and judge only what came after.

**Model step.** Each structured model call is written to a file and answered by a separate
Claude Opus session that sees only that call (`bench/live/answerer_prompt.md`), on a
subscription. No API spend.

**Approval.** A script stands in for the on-call's click: it approves exactly the proposal shown.
The point measured is that nothing changes without an approval record, not the approver's
judgment.

**Development.** Two flags only, declared before any test: `adFailure` and `cartFailure`. Both
runs found the right service, applied the fix after approval, and verify cleared.

**Test.** A separate agent writes a sealed schedule of incidents from the remaining fault flags
(`adHighCpu`, `adManualGc`, `emailMemoryLeak`, `intlShippingSlowdown`, `paymentFailure`,
`paymentUnreachable`, `productCatalogFailure`, `productCatalogLockContention`,
`recommendationCacheFailure`), with variants and the page delay. `live.inject sealed` applies
each without printing which. Every flag is reset and the shop rests between incidents. The
schedule is opened only by `live.score`, once, after the last incident ends.

**Reported per incident:** right service proposed; statistics-only top service (ablation); fixed
(the fault's flag turned off by an approved action); changes applied without approval (must be
0); verify's verdict; minutes from injection to the proposal.

## Test 2 (after test 1 was scored; test 1 is now development data)

Test 1 scored 6/10 (`results/test-1/`), with 0 unapproved changes. Every miss had the same cause:
the statistics blamed the caller of a failing service. Test 1 also had two false recoveries and
one fault that never fired (`productCatalogFailure` is switched by a targeting rule, not its
default variant). Changes, designed before any re-run and checked only on test-1 replays and
dev flags:

- `edges.py`: call edges between services, recovered from client span names and the HTTP client
  metric's `server_address`, with no service-specific rules.
- `blame.py`: a caller's errors and latency move to a callee only over an impaired edge, and only
  when the callee shows the fault itself: its own error or latency shift, a counted rise in its
  failed server calls, calls it no longer serves (vanished), or a container restart. Ties go to
  the service with more failed calls. The RCAEval scores are unchanged.
- `source.py`: the model sees each service's status, score, signals and these reasons. Evidence
  windows are clipped to the incident's own baseline. After a change, verify observes at least
  180 s after a 150 s settle, waits up to 6 minutes for impaired edges to carry traffic, and
  reports the worst rise in any edge's error ratio and the worst edge p99, not only the
  storefront's.
- Fault injection and the fix also set the flag's targeting rule.

Replay on test 1: 9 of 10 top-1 (the 10th never fired). Dev: `cartFailure` found and fixed; a
wrong-target change that fixed nothing reported error_rate 0.33, so verify does not clear it.

Test 2: 15 incidents from the same 9 flags, written by a separate agent, every flag at least
once, variants drawn fresh, page 180 to 240 s after the fault, 16 minutes of rest between
incidents. Scored once.
