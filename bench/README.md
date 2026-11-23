# Public benchmarks

Three tests of AgentOps' root-cause finding on systems and data it did not build. Each one was
developed on one part of the data, frozen, and then scored once on a part it had never seen.
Every model call was answered by Claude Opus on a subscription, so no API money was spent. The
protocols were written before scoring and sit next to the code; they also list every slip that
happened along the way.

| Test | What it is | Scored once on | Result | Best published |
|---|---|---|---|---|
| RCAEval | 735 recorded faults in three microservice shops; name the faulty service | RE2 + RE3, 360 cases | **94.7%** top-1 | 67 to 80% per shop on RE2 |
| Live shop | Faults switched on in the OpenTelemetry demo shop; find, fix after approval, check recovery | 15 blind incidents | **14 of 15** found and fixed, 0 unapproved changes | none (new test) |
| OpenRCA | 335 faults in banking, telecom and retail systems; name time, component and reason | 259 cases on 3 unseen systems | **40.9%** strict | 11.3% leaderboard; 43.9% newest paper |

## RCAEval (`rcaeval/`)

Developed on RE1 only; RE2 and RE3 scored once at commit `5f5e35c`. Service names were
anonymised so the model cannot lean on what a name suggests.

| Slice | Top-1 | Best published |
|---|---|---|
| RE2 Online Boutique | 0.989 | 0.667 |
| RE2 Sock Shop | 0.956 | 0.778 |
| RE2 Train Ticket | 0.900 | 0.800 |
| RE3 Online Boutique | 1.000 | |
| RE3 Sock Shop | 0.867 | |
| RE3 Train Ticket | 0.967 | |
| **All 360** | **0.947** (Avg@5 0.972) | |

The code's statistics alone reach 0.733; the model step fixed 78 cases and broke 1. Two slices
are under 93%, and RCAEval has no unused split to re-test them on.

## Live shop (`live/`)

The agent's graph runs unchanged against the OpenTelemetry demo shop, a microservice web shop
with its own monitoring. A separate agent writes a sealed schedule of faults; the harness
switches each one on through the shop's feature-flag service and pages the agent with one
sentence: customers report pages failing or slow. The agent has exactly one action, turning off
a named service's feature flags, behind the approval pause. A script stands in for the on-call
and approves exactly what was proposed, so what is measured is that nothing changes without an
approval record.

| Test | Result | What changed after it |
|---|---|---|
| 1 (10 incidents) | 6 of 10 | Blame follows call edges, so a caller is not blamed for a failing callee |
| 2 (stopped after 2) | 0 of 2, both wrongly marked recovered | Services restart between incidents; recovery is judged against the shop's own pre-fault latency; a symptom check is saved at the page |
| **3 (15 incidents)** | **14 of 15 found and fixed**; 13 of 13 where customers could see the fault | |

Test 3 in detail (`live/results/test-3/`, protocol in `live/PROTOCOL.md`):

- Plain RCAEval statistics alone would have named the right service in 7 of 15; the call-edge
  blame raises that to 14.
- 0 changes without approval, and verify never called an unfixed incident recovered.
- The miss was a 10-second shipping slowdown that showed no customer symptom at the page. The
  agent named no cause and handed the incident to a person instead of acting.
- Before the test, 5 cycles with no fault produced 0 false alarms on every check.

## OpenRCA (`openrca/`)

Microsoft's OpenRCA benchmark (ICLR 2025) asks for the time, the component and the reason of
each fault, scored by its own `evaluate.py`, which is vendored unchanged. Developed on Market
cloudbed-1 only; Market cloudbed-2, Telecom and Bank scored once at commit `c45ed15`. The agent
is never given the list of candidate components, which published baselines do give.

| System | Cases | Statistics alone | With the model step | Published |
|---|---|---|---|---|
| Market cloudbed-2 | 78 | 0.462 | **0.474** | 0.345 (RCAFlow), 0.297 (EviRCA) |
| Telecom | 51 | 0.314 | **0.392** | 0.471 (RCAFlow), 0.627 (EviRCA) |
| Bank | 136 | 0.360 | **0.368** | 0.404 (RCAFlow), 0.522 (EviRCA) |
| **All** | 259 | 0.386 | **0.409** | 0.113 (official leaderboard best), 0.439 (EviRCA) |

It beats every published Market figure and the official leaderboard, and trails the newest
papers on Bank and Telecom, whose formats were never seen in development. Published Market figures cover both cloudbeds;
ours cover cloudbed-2 only, since cloudbed-1 was development data. Six Bank answers were
seen by accident while the test was being designed; they are left out of the 259.
