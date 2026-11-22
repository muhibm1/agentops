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
