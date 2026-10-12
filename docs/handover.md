# Handover

For the team taking AgentOps over: how to run it, extend it, read what it did, re-measure it,
and what to watch once it touches a real system. The step-by-step detail for each extension is in
[CONTRIBUTING.md](../CONTRIBUTING.md); this page says what each one is for and what to check.

## Run it

Set up once (Python 3.11 or newer):

```
python -m venv .venv
source .venv/bin/activate            # .venv\Scripts\activate on Windows
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Investigate a scenario, then decide on the proposal:

```
agentops run --scenario clean_bad_deploy --model scripted --thread demo-1
agentops approve demo-1 --decision approve
```

`run` stops at the approval pause and prints the proposed action, its dry run and its idempotency
key; nothing changes until an operator decides. `approve` applies the action once, re-reads the
symptoms, and ends `resolved` with a postmortem, or goes back to investigate. `reject --reason`
and `edit --remediation-file` are the other two decisions, and `--operator` records the role
(`operator`, `on-call` or `reviewer`).

`--model scripted` replays answers written into each scenario file and needs no key. For a real
model, export `ANTHROPIC_API_KEY` in the shell and use `--model anthropic` (add `--dev` for the
cheaper model); see "The live model" in the [README](../README.md#the-live-model).

If a process dies mid-thread, `agentops resume <thread>` continues from the last checkpoint and
never applies an action twice. `agentops history <thread>` lists the checkpoints, and
`agentops fork <thread> --at <checkpoint> --set <hypothesis>=<score>` replays from any of them
with different hypothesis scores, under a new thread with its own ledger.

## Read what it did

Every thread writes two append-only files under `audit/`:

- `audit/<thread>.jsonl`, the audit log, one JSON object per line. In order, for an approved
  action: `proposal` (action, target, params, cited evidence ids, risk, idempotency key),
  `approval` (decision, operator role, whether the remediation was edited, reason), one
  `precondition` per check (observed value and pass or fail), `dry_run`, `apply` (applied, ok,
  effect), then `verify` (cleared or not, and which metrics and logs were still unhealthy). An
  escalation writes an `escalation` record with its reason instead.
- `audit/<thread>.ledger.jsonl`, the simulator's record of applied actions. With a real
  evidence source this would be the target system's own record.

Every record carries the incident id and the thread id. To answer "what did the agent change,
and who approved it?", read the `approval` and `apply` records with the same idempotency key.

## Extend it

- **A new action.** Add it to `runbooks/default.yaml`, the allowlist: an action not declared
  there can never be proposed, approved or applied, whatever the model says. Each entry needs its
  target pattern, typed parameters with limits, at least one precondition, a rollback and its
  risk. Treat a change to this file as a security change and review it as one. Never add an
  action to a prompt instead.
- **A new scenario.** One JSON file in `scenarios/` with the signals the agent sees, the ground
  truth it never sees, the evidence a correct diagnosis needs, the wrong actions, and the world
  after each action. Make it a trap where you can: a case the obvious answer gets right measures
  little.
- **A real evidence source.** Implement the `EvidenceSource` protocol in `src/agentops/sources/`
  (logs, metrics, deploys, dependencies, config, applying an action and its ledger). Everything it returns
  is untrusted data: the prompts fence evidence behind a random marker, and the registry blocks
  any action outside the runbook even when the model is fooled.

## Re-measure it

```
agentops eval --model scripted                    # free; checks the harness, not a model
agentops eval --model anthropic --dev             # prints the plan and estimated cost, stops
agentops eval --model anthropic --dev --yes       # spends it
```

The eval runs the graph and a ReAct baseline over the five dev scenarios and prints pass rates,
tool calls, latency, tokens and cost per system, then every failing case. Steer changes on dev
only. The three held-out scenarios (`evals/held_out/manifest.json`, pinned by
`evals/checksums.sha256`) are scored with `--held-out` only when a version is frozen; scoring
them to steer a change turns them into dev scores. How to read the results is in
[eval-methodology.md](eval-methodology.md).

## What to watch in production

- **Escalation rate.** Escalating on ambiguous evidence is a success, but a rate that climbs
  means the evidence sources or the budget no longer fit the incidents.
- **Approvals without reading.** The approval pause only protects you if someone reads the
  proposal. Watch for approvals that land seconds after the pause, and for the same operator
  approving everything.
- **Edited remediations.** An operator editing the proposed action often is a sign the model's
  proposals miss something the runbook or the evidence could carry.
- **`effect_unknown`.** An apply that raised is recorded with `applied: null` and escalates if
  the symptoms did not clear. Each one needs a person to check the target system by hand.
- **Spend.** Every model reply carries its token counts, and the eval prices them per model from
  a dated table in `src/agentops/pricing.py`. Recheck that table against the provider's pricing
  page when prices change.
- **What leaves the machine.** On a live run the prompts, the evidence, and any reject reason or
  edited remediation typed at approval go to the model provider. Keep secrets out of all three.
