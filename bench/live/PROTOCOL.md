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

**Test 2 was stopped after 2 of 15 incidents** (`results/test-2-stopped/`), 0 of 2, both marked
recovered by verify though neither was fixed, 0 unapproved changes. It is now development data.

- test-00, `emailMemoryLeak` 100x: the agent proposed payment (score 5.8 against email's 5.2).
  The storefront's p99 was 49 ms before, during and after: no customer-visible symptom at all.
- test-01, `adHighCpu`: the email leak from test-00 is memory inside the email process, which a
  flag reset does not free, so email's latency was still raised and email ranked first (17.5)
  over ad (16.2). The storefront's p99 went from 50 to 96 ms and stayed at 96 after the wrong
  change; verify cleared it because 96 ms is under the catalogue's absolute 500 ms.

## Test 3 (designed after test 2 was stopped, reviewed before any re-run)

Changes, all in the harness and the source adapter; the graph, the ranking statistics and the
blame rules are unchanged, so test2-00's ranking miss is a real miss that stays:

- **Restart between incidents.** After resetting every flag, `episode.sh` restarts the 14 shop
  containers and rests 18 minutes, so an in-process fault cannot leak into the next incident.
  This is harness hygiene between incidents, not something the agent does or knows about.
- **Quiet-shop gate.** Before each injection, `live.fired gate` must find no symptom over the
  last 3 minutes against the 10 before; it retries every 5 minutes three times, then voids the
  incident without injecting. Voids are reported.
- **Verify latency relative to baseline.** After a change, the source reports the paged
  storefront's p99 against its own pre-incident limit, max(1.5 x baseline p99, baseline p99 +
  25 ms), on verify's scale: the value is 500 plus the excess over that limit, so verify's
  unchanged 500 ms threshold falls exactly on the limit. The series id carries the measured p99
  and the limit, so the audit log shows what was measured. On every past incident this passes the
  six fixed ones (tightest 72 ms against 85.5) and fails test2-01 (96 against 75).
- **Fired, decided at the page.** `live.fired` applies verify's checks (error-ratio rise over
  0.01 on any call edge or the storefront, storefront p99 over its limit, any edge p99 over
  1000 ms, a new ERROR log pattern) to the 3 minutes before the page against the 10 before that.
  `harness open` writes the verdict to `fired.json` before the agent takes a step. This rule was
  written after test2-00, which it would exclude, and it is reported as such.
- **Scoring.** Right service and fixed is reported over all incidents and over fired incidents,
  side by side. If fewer than 10 of 15 fire, the all-incidents figure is the headline. For an
  incident that did not fire, verify's verdict is reported as "cannot judge", never "cleared",
  because a fix cannot be told from a fault nobody could see. The fired-only figure leaves out
  the subtlest faults and is biased upward.
- **"Fixed" for `emailMemoryLeak`** means the agent's approved action turned the flag off. The
  memory already leaked stays until the next restart, so verify may still see slowness.
- **Calibration with no fault**, before the test: 5 idle cycles under the same restart and rest,
  each judged as if paged and, 390 to 570 s later, as if a change had been made
  (`calibrate.sh`). The false "fired" and false "not cleared" rates are published with the test.

The schedule writer is told nothing new: 15 incidents from the same 9 flags, variants drawn
fresh, page 180 to 240 s after the fault, written by a separate agent and opened only by
`live.score`.

**Calibration result (before test 3, `results/calibration-test-3/`).** 5 idle cycles under the
restart protocol, no fault: the quiet-shop gate fired 0 of 5, the page-time symptom check fired 0
of 5, and the post-change check found nothing unhealthy 0 of 5. After a restart the storefront's
p99 sits at 75 to 103 ms (about 50 ms in a long-running shop), and the limits were 117 to 146 ms.
The live code is frozen at the commit that adds this paragraph.

## Test 3 result (scored once, 2026-11-23, frozen at 6c9796d)

15 incidents from a schedule written blind by a separate agent (sha256 a55e68b4...7513), every
one injected (0 voids), scored once by `live.score` (`results/test-3/`).

| | All 15 | Fired 13 (headline) |
|---|---|---|
| Right service proposed | 14 | 13 |
| Right service and fixed by an approved action | **14** | **13** |
| Edge-aware code ranking alone, top 1 | 14 | 12 |
| Plain RCAEval statistics alone, top 1 (ablation) | 7 | 5 |
| Changes applied without an approval record | 0 | 0 |
| Verify cleared an incident that was not fixed | 0 | 0 |

- The one miss, test-13 (`intlShippingSlowdown` 10sec), did not fire at the page; the agent named
  no cause and escalated to a person rather than act, which is the designed behaviour.
- test-01 (`adHighCpu`) also did not fire; the agent found and fixed ad anyway, and verify's
  verdict is reported as "cannot judge".
- test-14 (`adManualGc`) was found and fixed, but verify did not clear it (the error ratio stayed
  raised after the change) and the incident was escalated: a safe "not cleared", not a false one.
