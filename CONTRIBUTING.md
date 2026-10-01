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

Keep every name, host and author synthetic: use roles such as `ci-bot` and hosts such as
`checkout-api-7f3`, never a real person, email address or hostname. Then add the scenario to
the scenario tests in `tests/` and run the six commands.
