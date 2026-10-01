# Plan: Increment 1 scaffold

Change id: `2026-10-01-increment-1-scaffold`
Spec: [spec.md](./spec.md)
Branch: `wh/2026-10-01-increment-1-scaffold`
Worktree: one per task under `.worktrees/`, assigned by the conductor

## Approach

Wave 1 creates the package, the pins and the venv, because every later test needs an
interpreter with langgraph in it. Wave 2 runs four file-disjoint tasks in parallel: the state
model, the evidence source with its scenarios, the CI wiring, and the scripted model; none
imports another. Wave 3 wires the graph over all four. Wave 4 adds the CLI `run` command and
updates the documents that said "planned". Every task writes its tests first, runs the profile
`lint`, `typecheck` and `test` commands before committing, and names the eval cases it
implements. Pins: the values in task 1 were confirmed on PyPI on 2026-09-30; the builder re-runs
`python -m pip index versions <name>` for each and takes a newer release if one exists, noting
it in the commit body. Sensitive paths (profile `sensitive_paths`) are named in task headings;
the request authorises them (brief D1).

## Files

| Action | Path | Purpose |
|--------|------|---------|
| create | `pyproject.toml` (sensitive) | Package metadata, `agentops` entry point, ruff, pyright and pytest config |
| create | `requirements.txt`, `requirements-dev.txt` (sensitive) | Exact pins with licence comments; dev file ends with `-e .` |
| create | `LICENSE` (sensitive), `CONTRIBUTING.md`, `README.md`, `docs/ci.md` | Repository documents (R3) |
| create | `src/agentops/__init__.py`, `src/agentops/cli.py` | Version; Typer app (`--version` in task 1, `run` in task 7) |
| create | `src/agentops/state.py` (tier-2 floor) | State and models (R4 to R6), `CitationError`, `assert_cited` |
| create | `src/agentops/sources/__init__.py`, `base.py`, `scenario.py`, `simulator.py` | Protocol, value types, scenario schema, simulator (R7 to R10) |
| create | `scenarios/clean_bad_deploy.json`, `scenarios/deploy_red_herring_db.json` | Scenarios 1 and 2 with scripted outputs |
| create | `src/agentops/fakes.py` | `ScriptedChatModel`, `ScriptExhausted`, `scripted_model_for` (R11) |
| create | `src/agentops/nodes/__init__.py`, `triage.py`, `evidence.py`, `rca.py`, `src/agentops/graph.py` (tier-2 floor) | Nodes and `build_graph` (R12 to R14) |
| create | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.githooks/pre-push` (sensitive), `scripts/check_test_count.py` | CI, Dependabot, hook, floor script (R16 to R18) |
| create | `tests/conftest.py`, `tests/test_*.py` as named in evals.md | One test per eval case |
| modify | `CLAUDE.md`, `docs/sdlc/codebase-map.md` | Commands exist; "what exists today" table |
| modify | `.workhorse/profile.yml` (sensitive) | Commands confirmed, `format --check`, fake-model path (R22, brief D16) |

## Tasks

### Task 1: Package scaffold, pins and repository documents (sensitive: pyproject.toml, requirements*.txt, LICENSE)

- Requirement(s): R1, R2, R3
- Files: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `LICENSE`, `CONTRIBUTING.md`, `README.md`, `docs/ci.md`, `src/agentops/__init__.py`, `src/agentops/cli.py`, `tests/test_requirements.py`, `tests/test_repo_documents.py`
- Parallel: no
- Steps:
  1. Write failing tests: `tests/test_requirements.py` (E2) asserting the pin-and-licence pattern on both files; `tests/test_repo_documents.py` (E3) asserting the four documents and their required strings. Run with the system Python; confirm they fail because the files are missing.
  2. Create `requirements.txt`: `langgraph==1.2.12  # MIT`, `langchain-core==1.6.6  # MIT`, `pydantic==2.13.5  # MIT`, `typer==0.27.2  # MIT`. Create `requirements-dev.txt`: `pytest==9.1.1  # MIT`, `ruff==0.16.9  # MIT`, `pyright==1.1.414  # MIT`, `pip-audit==2.10.1  # Apache-2.0`, `pyyaml==6.0.3  # MIT`, then `-e .`. Re-check each with `pip index versions`; licence strings checked against the PyPI metadata of the pinned release.
  3. Create `pyproject.toml`: `[build-system] requires = ["setuptools==84.0.0"]`; project `agentops` 0.1.0, `requires-python = ">=3.11"`, `[project.scripts] agentops = "agentops.cli:app"`, `[tool.setuptools.packages.find] where = ["src"]`; `[tool.ruff]` line-length 100, target `py311`, select `E, F, I, UP, B`; `[tool.pyright]` `typeCheckingMode = "basic"`, `pythonVersion = "3.11"`, `include = ["src", "tests", "scripts"]`, `venvPath = "."`, `venv = ".venv"`; `[tool.pytest.ini_options]` `testpaths = ["tests"]`.
  4. Create `LICENSE` (MIT, "Copyright (c) 2026 Muhammad Muhibullah"), `CONTRIBUTING.md` (setup, `git config core.hooksPath .githooks`, the five commands, conventions from CLAUDE.md, how to add a scenario including `scripted_outputs`), `README.md` (four paragraphs: what it is, status "increment 1", how to run the demo command, link to the design), `docs/ci.md` (what CI runs, the floor and how to raise it, the `gh api repos/<r>/git/ref/tags/<tag>` command to re-resolve a pin, an empty "Accepted advisories" table, and a "Known gaps" list from the builder notes below: transitive pins unlocked, dev tools not audited, pyright fetches its node runtime, `SECURITY.md` due in increment 5).
  5. Create `src/agentops/__init__.py` with `__version__ = "0.1.0"` and `src/agentops/cli.py` with a Typer app exposing `--version` only.
  6. Run the profile `install` command. Confirm `git ls-files` shows no `.env*`, `.venv` or `*.db` file. Run `lint`, `typecheck`, `test`: green.
  7. Commit: `chore(scaffold): package, exact pins, licence and contributing guide`
- Done when: the profile install command succeeds from a clean checkout, E2 and E3 pass, lint and typecheck are clean.

### Task 2: State model and citation rule (tier-2 floor: src/agentops/state.py)

- Requirement(s): R4, R5, R6
- Files: `src/agentops/state.py`, `tests/test_state.py`
- Parallel: yes
- Steps:
  1. Write failing tests in `tests/test_state.py`: E4 (`new_incident` defaults), E5 (recursive schema walk against `CONTROL_FIELDS`), E6 (role operator accepted), E7 (score bounds and empty citations rejected), E8 (username, email and case variant rejected). Confirm they fail on import.
  2. Implement `state.py` per spec Architecture: the ten models, `IncidentState` with `opened_at`, `CONTROL_FIELDS`, `MODEL_OUTPUT_SCHEMAS = (Triage, RcaOutput, Postmortem)`, `new_incident`, `CitationError`, `assert_cited`. `Approval.operator` is `Literal["operator", "on-call", "reviewer"]`. `RCA.label` and `Remediation.action` match `^[a-z0-9_]+$`.
  3. Green; lint, typecheck, full suite.
  4. Commit: `feat(state): IncidentState, model-output schemas and the citation rule`
- Done when: E4 to E8 pass and pyright reports no error in `state.py`.

### Task 3: Evidence source protocol, simulator and scenarios 1 and 2

- Requirement(s): R7, R8, R9, R10
- Files: `src/agentops/sources/__init__.py`, `src/agentops/sources/base.py`, `src/agentops/sources/scenario.py`, `src/agentops/sources/simulator.py`, `scenarios/clean_bad_deploy.json`, `scenarios/deploy_red_herring_db.json`, `tests/test_simulator.py`, `tests/test_scenarios.py`
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_simulator.py` E9, E10, E11, E12, E13, E14, E15, E38 (two sources from one `Scenario`; apply on one leaves the other and the `Scenario` unchanged); `tests/test_scenarios.py` E16, E17 (E18 is added in task 6). Confirm they fail on import.
  2. Implement `base.py` (protocol and value models, `runtime_checkable`), `scenario.py` (`Scenario` tree with `extra="forbid"`, a validator for unique signal ids and for `required_evidence` membership, `load_scenario`, `list_scenario_ids`), `simulator.py` (deep-copies `signals` at construction; filters per R8; `apply` per R9 using `post_action_world`).
  3. Write scenario 1 (`checkout`, incident at 2026-03-10T10:30:00Z: deployment `deploy-001` v2.15.0 at 10:12 whose diff adds a shipping-option handler; eight log lines, five of them `KeyError: 'shipping_option'` after 10:13; series `error_rate` rising to 0.12, `p99_latency_ms` doubling, `db_pool_utilisation` flat at 0.40; dependencies healthy; ground truth `bad_deploy` / `rollback_deploy`; `required_evidence` `["deploy-001", "log-003"]`; `wrong_actions` `["scale_db_pool"]`; post-action world for `rollback_deploy` restoring `error_rate` to 0.002) and scenario 2 (`payments`, incident at 2026-03-10T14:20:00Z: deployment `deploy-001` at 13:58 whose diff changes only a log level; log lines `connection pool exhausted (64/64)` from 14:02; `db_pool_utilisation` 0.97 from 14:02, `error_rate` 0.06, `p99_latency_ms` 4x; dependency `dep-postgres` degraded; ground truth `database_saturation` / `scale_db_pool`; `required_evidence` `["metric-db_pool_utilisation", "deploy-001"]`; `wrong_actions` `["rollback_deploy"]`; post-action world for `scale_db_pool` dropping utilisation to 0.55). Both carry `scripted_outputs` whose `rca` cites ids from `required_evidence`. All names, hosts and authors synthetic (`ci-bot`, `checkout-api-7f3`).
  4. Green; lint, typecheck, full suite.
  5. Commit: `feat(sources): EvidenceSource protocol, SimulatorSource and scenarios 1 and 2`
- Done when: E9 to E17 pass and `isinstance(SimulatorSource(...), EvidenceSource)` is true.

### Task 4: CI workflow, Dependabot, pre-push hook and the test-count floor (sensitive: .github/**, .githooks/**)

- Requirement(s): R16, R17, R18
- Files: `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.githooks/pre-push`, `scripts/check_test_count.py`, `tests/test_ci_workflow.py`, `tests/test_check_test_count.py`
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_ci_workflow.py` E30, E31, E33; `tests/test_check_test_count.py` E32. Confirm they fail because the files are missing.
  2. Create `scripts/check_test_count.py` (stdlib only: `xml.etree`, `sys`; passed = tests - skipped - failures - errors across all `testsuite` elements; prints count and floor; exit 1 below the floor).
  3. Create `ci.yml` per spec Interfaces: `on: [push, pull_request]`, `permissions: contents: read`, `env: MIN_TESTS: "30"`, `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`, `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0` with `python-version: "3.11"`, then the eight `run` steps through `.venv/bin/python`. The checkout step sets `with: persist-credentials: false` (the token must not stay in `.git/config`); the file never uses `pull_request_target` (E30, E31 assert both).
  4. Create `dependabot.yml` (`pip` on `/`, `github-actions` on `/`, both `weekly`) and `.githooks/pre-push` (`#!/bin/sh`, `set -e`, choose `.venv/Scripts/python.exe` or `.venv/bin/python`, run `ruff check .` then `pytest -q`). Mark it executable in the index: `git update-index --chmod=+x .githooks/pre-push`.
  5. Green; lint, typecheck, full suite.
  6. Commit: `ci: workflow with SHA-pinned actions, test-count floor, dependabot and pre-push hook`
- Done when: E30 to E33 pass and `git ls-files -s .githooks/pre-push` shows mode 100755.

### Task 5: Scripted chat model

- Requirement(s): R11
- Files: `src/agentops/fakes.py`, `tests/test_fakes.py`
- Parallel: yes
- Steps:
  1. Write failing tests `tests/test_fakes.py` E19, E20 using two small local Pydantic schemas (not `state.py`, which this task must not import).
  2. Implement `ScriptedChatModel(BaseChatModel)`: `script` entries normalised to JSON text at construction; `_generate` pops the next entry into an `AIMessage` (raise `ScriptExhausted` when empty) and appends the messages to `calls`; `_llm_type` returns `"scripted"`; `with_structured_output(schema, *, method="json_schema", include_raw=False, **kwargs)` returns a `RunnableLambda` that invokes the model and returns `schema.model_validate_json(content)`; `include_raw=True` raises `NotImplementedError`. No `os.environ` access.
  3. Green; lint, typecheck, full suite.
  4. Commit: `feat(fakes): scripted chat model with structured-output parsing`
- Done when: E19 and E20 pass and pyright accepts `ScriptedChatModel` where `BaseChatModel` is expected.

### Task 6: Nodes and the linear graph (tier-2 floor: src/agentops/graph.py)

- Requirement(s): R12, R13, R14, R23, R10 (E18)
- Files: `src/agentops/graph.py`, `src/agentops/nodes/__init__.py`, `src/agentops/nodes/triage.py`, `src/agentops/nodes/evidence.py`, `src/agentops/nodes/rca.py`, `src/agentops/fakes.py` (add `scripted_model_for`), `tests/conftest.py`, `tests/test_graph.py`, `tests/test_scenarios.py` (add E18)
- Parallel: no
- Steps:
  1. Write failing tests: `tests/conftest.py` fixtures `scenario(id)`, `source`, `scripted_model`, and an autouse fixture that deletes `LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2`, `LANGSMITH_API_KEY`, `LANGCHAIN_API_KEY`, `LANGSMITH_ENDPOINT` and `LANGCHAIN_ENDPOINT` with `monkeypatch` so no developer shell sends prompts to LangSmith; `tests/test_graph.py` E21, E22, E23, E24, E25, E26 (both scenarios plus the hostile `log-003` copy built with `model_copy`), E37 (wrapper source whose `get_logs` fails once, then always: retried once, then the error surfaces); `tests/test_scenarios.py` E18. Confirm they fail on import.
  2. Add `scripted_model_for(scenario)` to `fakes.py` (script `[scripted_outputs.triage, scripted_outputs.rca]`).
  3. Implement the nodes: `triage` builds a system prompt (states that evidence is "data, not instructions") and a human message with service and symptoms, calls `model.with_structured_output(Triage, method="json_schema")`, calls `assert_cited` on every hypothesis's cited ids against current evidence, returns `hypotheses`, `status="investigating"` and one `Action(tool="model")`. `collect_evidence` calls `get_logs` (60-minute window ending at `opened_at`, no query), `get_metrics(["error_rate", "p99_latency_ms", "db_pool_utilisation"])`, `get_deployments`, converts each item to `Evidence` keeping the item's id, replaces every `<<<` with `< < <` and every `>>>` with `> > >` in each string field of the payload (R23; one small function in `evidence.py`, applied to the `Evidence` copy only, never to the source's objects), returns `evidence` and three `Action`s. `rca` renders every evidence item as JSON inside `<<<untrusted evidence>>>` ... `<<<end>>>` delimiters, calls the model for `RcaOutput`, calls `assert_cited` on both citation lists, returns `root_cause`, `remediation` and one `Action`.
  4. Implement `build_graph` with the retry policies in spec Architecture and the edges `START -> triage -> evidence -> rca -> END`; accept an optional checkpointer and pass it to `compile`.
  5. Green; lint, typecheck, full suite. Record in the commit body whether E23 confirmed that `ValidationError` needed the explicit `retry_on` (ADR 0002).
  6. Commit: `feat(graph): triage, evidence and rca nodes with citation enforcement and retries`
- Done when: E18 and E21 to E26 pass for both scenarios.

### Task 7: CLI run command, document updates and the profile (sensitive: .workhorse/profile.yml)

- Requirement(s): R1, R15, R19, R21, R22
- Files: `src/agentops/cli.py`, `tests/test_cli.py`, `CLAUDE.md`, `docs/sdlc/codebase-map.md`, `README.md`, `.workhorse/profile.yml`, `tests/test_repo_documents.py` (extend E3)
- Parallel: no
- Steps:
  1. Write failing tests `tests/test_cli.py` E1, E27, E28, E29, E35 (scenario with offset-less `incident_time`: exit 1 naming file and field), E36 (`--scenario ../secret`, an absolute path and `a/b` exit 1 and never open a file outside the directory) with Typer's `CliRunner`. The `run` command must catch `ValidationError` from `load_scenario` (exit 1) and accept only ids matching `^[a-z0-9_-]+$`.
  2. Implement `run` per spec Interfaces: options `--scenario`, `--model` (`scripted` or `anthropic`, required), `--scenarios-dir` (default `scenarios`); exit codes 0, 1, 2; the printed report; `anthropic` exits 1 with the increment-2 sentence.
  3. Update `CLAUDE.md` "Commands" (remove the "Status: planned" line, add the demo command `agentops run --scenario clean_bad_deploy --model scripted`) and `docs/sdlc/codebase-map.md` ("What exists today" lists the new paths; the stale `.gitignore` sentence corrected). Update `README.md` usage if the final command differs from task 1's text.
  4. Extend E3 in `tests/test_repo_documents.py` with the three profile assertions (fails against the current profile), then edit `.workhorse/profile.yml` (R22, brief D16): run each `commands:` entry as written and remove its "(believed)" label only once it has exited 0; change `format` to `.venv/Scripts/python.exe -m ruff format --check .` and name `ruff format .` in `CONTRIBUTING.md` as the local fix command; change the style note to `src/agentops/fakes.py`; update `stack.ci` and `notes` so they no longer say the files do not exist. Touch nothing else in the profile.
  5. Green; run every profile command (E34) and time the suite and the demo command (N1, N2); record the figures in the commit body.
  6. Commit: `feat(cli): agentops run reaches rca with the scripted model` (the profile edit goes in a second commit, `chore(workhorse): profile matches the built repository`)
- Done when: E1, E3, E27 to E29, E35, E36 and E34 pass, N1 and N2 are within target, `CLAUDE.md` no longer says the commands are planned, and the profile carries no "(believed)" under `commands:`.

## Waves

| Wave | Tasks | Files (disjoint within the wave) |
|---|---|---|
| 1 | T1 | pyproject, requirements, LICENSE, CONTRIBUTING, README, docs/ci.md, `__init__.py`, `cli.py`, two test files |
| 2 | T2, T3, T4, T5 | `state.py` + `test_state.py`; `sources/*`, `scenarios/*`, `test_simulator.py`, `test_scenarios.py`; `.github/*`, `.githooks/*`, `scripts/*`, `test_ci_workflow.py`, `test_check_test_count.py`; `fakes.py` + `test_fakes.py` |
| 3 | T6 | `graph.py`, `nodes/*`, `fakes.py`, `conftest.py`, `test_graph.py`, `test_scenarios.py` |
| 4 | T7 | `cli.py`, `test_cli.py`, `test_repo_documents.py`, `CLAUDE.md`, `codebase-map.md`, `README.md`, `.workhorse/profile.yml` |

Wave 2 holds four tasks, equal to `build.max_parallel`.

## Verification plan

The verifier runs the profile `lint`, `format` (with `--check`), `typecheck`, `test` and
`security_audit` commands and reports exit codes; then every E case through `test`, N1 from the
pytest summary line, N2 by timing the demo command. The human sees `verification.md` with the
per-category pass counts (golden 18, edge 5, failure 8, adversarial 7, non-functional 2) and,
once the branch is pushed for the gate, the GitHub Actions run for `ci.yml` (R20).

## Rollback

Nothing is deployed. Dev: `git revert` of the merge commit on `main` removes every file; no
data, no schema. If only CI misbehaves after merge, deleting `.github/workflows/ci.yml` in a
`ci:` commit stops it without touching the package. Staging and prod: not applicable, no
environment exists (profile `environments`).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A pin from 2026-09-30 is superseded or yanked on build day | Low | Install fails | Builder re-runs `pip index versions` in task 1 and takes the newer release |
| `pydantic.ValidationError` not retried by LangGraph's default predicate | Believed likely | Retry claim false | Explicit `retry_on` (ADR 0002); E23 proves it either way |
| `pip-audit` flags a transitive advisory with no fix | Medium | CI red | `--ignore-vuln <id>` plus a row in `docs/ci.md`; never a blanket skip |
| pyright under `pip` cannot fetch its node runtime in CI | Low | CI red | Re-run; if persistent, switch the step to `npx pyright` pinned to the same version, as a `ci:` follow-up |
| Harness refuses to write a sensitive filename | None expected: no `.env`, `.npmrc` or key file is in the edit list | | `.env.example` deferred to increment 2 (brief D8) |
| Test count lands below 30 after merges | Low | Hollow green becomes CI red, which is the intent | Planned tests exceed 38 with parametrisation; `docs/ci.md` says how to adjust the floor deliberately |

## Builder notes

From the constraint audit's low findings; none changes a requirement beyond R16, R22 and R23.

- Task 4: `persist-credentials: false` on the checkout step; never introduce `pull_request_target`.
- Task 6: `tests/conftest.py` clears the LangSmith and LangChain tracing variables (step 1). Tests only; the CLI leaves the environment alone, and increment 2 decides tracing.
- Task 1: `docs/ci.md` "Known gaps" records as accepted or deferred: transitive packages are not locked (constraints.md known debt); `pip-audit` covers `requirements.txt` only, not the dev tools or the `setuptools` build backend; the `pyright` wrapper fetches its node runtime on first run (believed, not verified); `SECURITY.md` with a disclosure contact arrives before the repository goes public in increment 5.
- Verifier: list R20 (green Actions run) under "Not verified" until the owner reads the run.
