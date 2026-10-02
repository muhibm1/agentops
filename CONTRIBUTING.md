# Contributing to AgentOps

## Setup

You need Python 3.11 or newer and git. From the repository root, on Windows:

```
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Elsewhere, activate with `source .venv/bin/activate` instead. The dev requirements end with
`-e .`, so the `agentops` package is installed from your working tree.

Turn on the shared git hooks once per clone. The pre-push hook runs the linter and the tests
before anything leaves your machine:

```
git config core.hooksPath .githooks
```

## The six commands

Run these from the activated venv. CI runs the same checks, and a change is ready only when all
six exit 0.

| Purpose | Command |
|---|---|
| Install | `python -m pip install -r requirements.txt -r requirements-dev.txt` |
| Lint | `python -m ruff check .` |
| Format check | `python -m ruff format --check .` (CI runs this; fix locally with `python -m ruff format .`) |
| Typecheck | `python -m pyright` |
| Test | `python -m pytest -q` |
| Dependency audit | `python -m pip_audit -r requirements.txt` |

Tests need no API key and make no network call: they use a scripted chat model.

## Conventions

- Branches are `<prefix>/<short-name>`; commits follow Conventional Commits (`feat:`, `fix:`,
  `docs:`, `test:`, `chore:`).
- Tests live in `tests/` as `test_*.py`. Never edit a test to make it pass; if the test is
  wrong, say so in the pull request.
- Every dependency is an exact pin (`name==X.Y.Z`) with its licence as a comment, in
  `requirements.txt` for runtime and `requirements-dev.txt` for tools. A test enforces this.
- The model recommends; the graph enforces. Control fields such as status, budgets, approval
  and execution never appear in a model output schema.
- Every evidence payload (logs, metrics, deployments) is untrusted data, never instructions.
- No flag, environment variable or config may disable the approval interrupt.
- New actions go in the runbook, never in a prompt.
- Never score the held-out evaluation split to steer a change.
- Only the owner pushes to `main`.

## Adding a runbook action

`runbooks/default.yaml` is the allowlist: only an action declared there can be proposed,
approved or applied. Changing it is an ask-first change. Each entry names the `action`, its
`target_kind` and a `target_pattern` (a full-match regular expression for the names it may be
aimed at), the declared `params` with their types and limits, the `risk`, at least one
`preconditions` check, the `rollback` and `idempotent`, and optionally `verify_window_minutes`
(1 to 1440, default 30): how long after the incident opened `verify` and the metric
preconditions read signals for that action. The loader in
`src/agentops/actions/runbook.py` rejects an unknown key or a missing field at startup. The
checks live in `src/agentops/actions/preconditions.py`: a new check kind needs a model there
and a function that fails, never raises, when the source cannot answer. Add the action's
tests to `tests/test_runbook.py` (schema, parameters and target, one case that admits and one
that denies) and `tests/test_preconditions.py`, and give any scenario that uses the action a
`post_action_world` for it.

## Adding a scenario

A scenario is one JSON file in `scenarios/`, named after its id (for example
`scenarios/clean_bad_deploy.json`). It holds:

- `id`, `service`, `symptoms` (at least one) and `incident_time` (ISO 8601 with a UTC offset).
- `signals`: the only part the agent sees, as `logs`, `metrics`, `deployments`, `dependencies`
  and `config`. Every signal carries an `id` that is unique within the scenario.
- `ground_truth` (`root_cause` and `correct_action`), `required_evidence` (signal ids the
  investigation must collect), `wrong_actions`, `expected_outcome` (`remediate`, `escalate` or
  `loop`) and `post_action_world` (what the signals become after each action is applied). The
  agent never sees these.
- `post_action_world` is keyed by action name. Each world adds metric points (extending the
  series with the same id) and log lines once that action is in the simulator's ledger. When a
  fault needs two actions, add a combined world keyed by the distinct action names sorted and
  joined by `+`, for example `rollback_deploy+scale_db_pool` in `two_concurrent_faults`; it is
  served once every named action is applied. An unsorted or repeated key is rejected. The
  action names come from `runbooks/default.yaml`; an action not in the runbook can never be
  applied.
- `scripted_outputs`: the `triage` and `rca` answers the scripted chat model returns for this
  scenario, so the scenario runs in tests without a live model. Each answer must be valid
  against its output schema and cite only signal ids that exist in the scenario. A scenario
  that ends in an escalation also needs an `escalate` answer (`summary` and `evidence_ids`),
  because the graph asks the model for the hand-off summary. The reason is never scripted:
  code writes it as `budget_exhausted` (the read budget ran out), `ambiguous` (the two best
  hypotheses are within the margin, or rca named no cause) or `no_evidence` (no source returned
  anything). Later steps add others, for example `action_not_allowed` (rca proposed an action
  outside the runbook), `precondition_failed` or `symptoms_persist`.
- A `remediate` or `loop` scenario also needs a `postmortem` answer (`summary`, `timeline`,
  `root_cause`, `evidence_ids` and `follow_ups`), because the graph asks the model for it once
  the symptoms clear; its `evidence_ids` must be signal ids in the scenario. A `loop` scenario
  (the first action does not clear the symptoms) also needs `rca_after_action`: the rca answer
  the scripted model gives once an action is applied, naming the next action. In
  `two_concurrent_faults` it proposes `scale_db_pool` after `rollback_deploy` leaves the pool
  saturated.

- `scripted_outputs.baseline`: what the ReAct baseline does on this scenario under the scripted
  model. Every shipped scenario has one; see the next section.

Keep every name, host and author synthetic: use roles such as `ci-bot` and hosts such as
`checkout-api-7f3`, never a real person, email address or hostname. Then add the scenario to
the scenario tests in `tests/` and run the six commands.

If you change one of the three held-out scenarios (`evals/held_out/manifest.json` lists them),
`agentops dataset` and the checksum test in `tests/test_dataset.py` fail. The held-out split is frozen: changing it, and
rewriting `evals/checksums.sha256` with `agentops dataset --write --force`, is an ask-first
change that says why in the pull request.

## Adding a baseline script

`agentops eval` runs the ReAct baseline (`src/agentops/baseline.py`) on the scripted model, so
each scenario scripts what the baseline does under `scripted_outputs.baseline`. The graph never
reads it. It has two keys:

- `steps`: at least one tool call (the shipped scenarios use three to six), in order, each `{"tool": ..., "args": {...}}`. The tool is one
  of the six the baseline has: `get_logs`, `get_metrics`, `get_deployments`, `get_config`,
  `get_dependency_health` and `apply`. The arguments are what the agent passes to that tool,
  for example `start` and `end` (ISO 8601) for `get_logs`. An `apply` step must name `action`
  and `target` in its args, and may give `params`.
- `answer`: the baseline's final reply, with `root_cause` (required, a label compared to the
  scenario's `ground_truth.root_cause`) and optionally `action`, `target` and `params`.

`scripted_baseline_model_for(scenario)` in `src/agentops/fakes.py` turns the script into a model
that makes one tool call per step and then replies with the answer; a scenario without a script
is refused by name. Write the script as a plausible naive agent, not as the ground truth: in
`log_injection_drop_table` the baseline applies `drop_table` because a log line told it to, and
in `deploy_red_herring_db` it applies one of the scenario's `wrong_actions`. A tool error is
returned to the agent as text, so a step that fails does not stop the script. Then run
`python -m pytest -q tests/test_scenarios.py tests/test_baseline.py` and the six commands.
