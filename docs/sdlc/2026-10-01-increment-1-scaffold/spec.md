# Spec: Increment 1 scaffold

Change id: `2026-10-01-increment-1-scaffold`
Intent: the request in [conductor-log.md](./conductor-log.md) (no intent.md was written)
Status: draft
Policy skills applied: wh-agent-rules, wh-security-baseline, wh-readable-code, wh-adr, wh-evals

## Summary

Turn the approved design (`docs/superpowers/specs/2026-09-29-agentops-design.md`, cited as
"design §n") into a running Python 3.11 package: the state model, the evidence-source protocol
with its simulator and two scenarios, a scripted fake chat model, a three-node linear graph, a
CLI that reaches root-cause analysis without an API key, and CI that proves all of it on every
push. The one decision that matters: the scripted model lives inside the package and its answers
live inside the scenario files, so the CLI demo and the unit tests exercise one path (ADR 0001).

Everything below is `believed, not verified` where it describes code that does not exist yet.
Confirmed facts are labelled. The repository today holds only documents (confirmed: `git ls-files`).

## Requirements

| ID | Requirement | Acceptance check | Source |
|----|-------------|------------------|--------|
| R1 | The repository SHALL be an installable package: the profile `install` command succeeds, `import agentops` exposes `__version__`, and `agentops --help` exits 0 | E1 | request; design §13 |
| R2 | Every line in `requirements.txt` and `requirements-dev.txt` SHALL be an exact pin `name==X.Y.Z` followed by a `# <licence>` comment; the only exception is `-e .` in the dev file | E2 | request; CLAUDE.md conventions |
| R3 | `LICENSE` (MIT, owner's name), `CONTRIBUTING.md` (setup, hook activation, commands, conventions), a stub `README.md` and `docs/ci.md` (accepted advisories table) SHALL exist | E3 | request; design §13 |
| R4 | `src/agentops/state.py` SHALL define `IncidentState` and the models `Hypothesis`, `Triage`, `RCA`, `Remediation`, `RcaOutput`, `Postmortem`, `Evidence`, `Action`, `Approval`, `ExecutionRecord`; `new_incident()` SHALL start at status `triage`, budget 12, loops 0 | E4 | design §5 |
| R5 | No model-output schema (`Triage`, `RcaOutput`, `Postmortem`) SHALL contain any control field (`status`, `investigation_budget`, `loops`, `approval`, `execution`) at any nesting depth | E5 | design §5; constraints "must not change" 3 |
| R6 | Validation SHALL reject: a hypothesis score outside [0, 1]; an `RCA` or `Remediation` with no evidence id; an `Approval.operator` that is not one of the role literals `operator`, `on-call`, `reviewer` | E6, E7, E8 | design §5; constraints Q4 |
| R7 | `src/agentops/sources/base.py` SHALL define the `EvidenceSource` protocol with the seven methods of design §6 and their value types; `SimulatorSource` SHALL satisfy it at runtime and under pyright | E9 | design §6 |
| R8 | `SimulatorSource` SHALL serve logs filtered by window and optional case-insensitive query, metrics by name and window, deployments by window, config and dependency health; every evidence item SHALL carry the id declared in the scenario file; no protocol method SHALL return ground truth, grading keys or scripted outputs | E10, E11, E12 | design §6, §17 |
| R9 | `apply(action, key)` SHALL mutate served signals to the scenario's `post_action_world` for that action and append to the ledger; a repeated key SHALL apply once and report `applied=false`; an action with no post-action world SHALL return `ok=false` with no mutation and no ledger entry; `apply` never raises | E13, E14, E15 | design §6, §7 |
| R10 | `scenarios/clean_bad_deploy.json` and `scenarios/deploy_red_herring_db.json` SHALL validate against a `Scenario` model with unknown keys forbidden; signal ids unique; `required_evidence` ids present in signals; `correct_action` not in `wrong_actions`; scripted outputs valid against `Triage` and `RcaOutput` and citing only declared ids | E16, E17, E18 | design §6 |
| R11 | `ScriptedChatModel` SHALL return its scripted outputs in order through `with_structured_output(schema, method="json_schema")`, record every prompt it receives, raise `ScriptExhausted` when empty, raise `pydantic.ValidationError` on a malformed entry, and read no environment variable and open no socket | E19, E20, E26 | design §11; profile style_notes |
| R12 | `build_graph(source, model)` SHALL compile `START -> triage -> evidence -> rca -> END`; a run of either scenario with the scripted model SHALL end with at least one hypothesis, every `required_evidence` id in `evidence`, `root_cause.label` equal to the ground truth, `remediation.action` equal to `correct_action`, `status` `investigating`, and `actions` recording two model calls and three source calls | E21, E22 | request; design §4, §16 |
| R13 | A model output citing an evidence id absent from state, or an `RCA`/`Remediation` with an empty citation list, SHALL raise `CitationError` inside the node; model nodes carry `RetryPolicy(max_attempts=3, retry_on=(CitationError, ValidationError))`; a bad-then-good script succeeds with the good output; three bad outputs surface `CitationError` to the caller | E23, E24, E25 | design §4, §5 |
| R14 | No prompt sent to the model SHALL contain the scenario id, ground truth, correct or wrong actions, required-evidence list or scripted outputs; evidence payloads SHALL be rendered inside a delimited block the system prompt names as untrusted data | E26 | design §5, §17; constraints 7 |
| R23 | The evidence node SHALL replace every occurrence of `<<<` and `>>>` in payload text with `< < <` and `> > >` before the item enters state, so no payload can contain or close the delimiters of R14; a run whose log line contains `<<<end>>>` SHALL produce an rca prompt with exactly one opening and one closing delimiter (audit low 6; D17) | E26 | audit finding; constraints 7 |
| R15 | `agentops run --scenario <id> --model scripted` SHALL exit 0 and print hypotheses, evidence count, root-cause label and remediation action; an unknown scenario exits 1 listing the available ids; `--model anthropic` exits 1 saying the live model arrives in increment 2; a run that fails after retries exits 2 with the error | E27, E28, E29 | request; design §16 |
| R16 | `.github/workflows/ci.yml` SHALL run one job with `permissions: contents: read` on `push` and `pull_request`, steps in order: venv install, `ruff check`, `ruff format --check`, `pyright`, `pytest --junitxml`, test-count floor, `pip-audit -r requirements.txt`; the checkout step sets `persist-credentials: false`; every `uses:` pinned to a 40-hex commit SHA with the tag in a comment; the strings `secrets.` and `pull_request_target` absent from the file | E30, E31 | request; design §13; audit low 7 |
| R17 | `scripts/check_test_count.py <junit.xml> <floor>` SHALL exit 1 when passed tests are fewer than the floor and 0 otherwise; CI sets the floor to 30 | E32 | design §13 |
| R18 | `.githooks/pre-push` SHALL be executable (index mode 100755) and run `ruff check` then `pytest`; `.github/dependabot.yml` SHALL cover `pip` and `github-actions` weekly; `CONTRIBUTING.md` names `git config core.hooksPath .githooks` | E33 | request; design §13 |
| R19 | The profile commands `lint`, `format` (as `--check`), `typecheck`, `test` and `security_audit` SHALL all exit 0 on the change branch | E34 | profile commands |
| R20 | The GitHub Actions run for the change branch SHALL be green | Owner reads the Actions run; no machine case in this repository can observe it | request |
| R21 | The full test suite SHALL finish in under 60 s and `agentops run` with the scripted model in under 5 s on the owner's machine | N1, N2 | request ("minimal"); CI cost |
| R22 | `.workhorse/profile.yml` SHALL match the built repository: every command under `commands:` runs as written on the change branch and loses its "(believed)" label; `format` is `ruff format --check .`; the style note names `src/agentops/fakes.py`; `stack.ci` and `notes` no longer say the files do not exist (D16) | E3, E34 | brief Outcome; audit medium |

## Design

### Architecture

Design §13 layout, limited to what increment 1 imports. Paths are new unless marked.

- `src/agentops/state.py`: the §5 state and Pydantic models, `new_incident()`, `CitationError`,
  `assert_cited(ids, evidence)`, and two exported constants `MODEL_OUTPUT_SCHEMAS` and
  `CONTROL_FIELDS` so a test can prove R5 by inspection rather than by convention. One addition
  to §5: `opened_at: datetime`, the moment the incident opened, so evidence windows are
  deterministic (D11).
- `src/agentops/sources/base.py`: `EvidenceSource` (`typing.Protocol`, `runtime_checkable`) and
  the value models `Window`, `LogLine`, `Series`, `Point`, `Deployment`, `DependencyStatus`,
  `ActionRequest`, `ActionResult`, `AppliedAction`. No dependency on `state.py`.
- `src/agentops/sources/scenario.py`: `Scenario` and its sub-models (`Signals`, `GroundTruth`,
  `PostActionWorld`, `ScriptedOutputs`), all `extra="forbid"`; `load_scenario(path)`,
  `list_scenario_ids(directory)`.
- `src/agentops/sources/simulator.py`: `SimulatorSource(scenario)`. Holds a mutable copy of
  `signals` and a ledger list. Serves only `signals`; `ground_truth`, `required_evidence`,
  `wrong_actions`, `expected_outcome`, `post_action_world` and `scripted_outputs` have no
  accessor on the source.
- `src/agentops/fakes.py`: `ScriptedChatModel(BaseChatModel)` and `scripted_model_for(scenario)`.
  Lives in the package, not `tests/fakes.py` as design §13 says, because the CLI needs it
  (ADR 0001). Implements `_generate` and `_llm_type` (confirmed required by the LangChain
  reference) and overrides `with_structured_output` to parse the next scripted JSON text with
  `schema.model_validate_json`, because the base implementation needs `bind_tools` (confirmed).
- `src/agentops/nodes/triage.py`, `evidence.py`, `rca.py`: one function each,
  `triage(state, *, model)`, `collect_evidence(state, *, source)`, `rca(state, *, model)`.
  Prompts are module constants. Each node appends one `Action` per model or source call.
  `collect_evidence` neutralises `<<<` and `>>>` in every payload string (R23) so the rca
  renderer's delimiters cannot be forged by a log line; the source keeps the original text.
- `src/agentops/graph.py`: `build_graph(source, model, checkpointer=None)` binds the nodes with
  `functools.partial`, adds `RetryPolicy(max_attempts=3, retry_on=(CitationError,
  ValidationError))` on `triage` and `rca` and `RetryPolicy(max_attempts=2)` on `evidence`, and
  compiles. The explicit `retry_on` exists because `pydantic.ValidationError` subclasses
  `ValueError`, which LangGraph's default predicate is believed not to retry (ADR 0002).
- `src/agentops/cli.py`: Typer app, commands `run` and `--version`. `run` loads the scenario,
  builds `SimulatorSource` and the scripted model, calls `new_incident`, invokes the graph, prints.
- `scripts/check_test_count.py`: reads JUnit XML, exits 1 below the floor.
- Modified: `CLAUDE.md` (commands now exist, the demo command), `docs/sdlc/codebase-map.md`
  ("what exists today" table), `.workhorse/profile.yml` (sensitive; R22, D16). `.gitignore`
  already covers `.env*`, `.venv/`, `*.db`, `audit/` (confirmed: committed on `main`, `git diff`
  empty); no edit.

Not in this increment: `langgraph.json` and the build check (D7), `.env.example` (D8),
`models.py` and the live model, the router, parallel evidence nodes, merge, approval, execute,
verify, postmortem, escalate, the runbook, checkpoint persistence, LangSmith, evals.

### Data

No database. Two files are read at run time and one is written by nothing yet.

| Store | Shape | Read by | Written by |
|---|---|---|---|
| `scenarios/<id>.json` | `Scenario` below | `load_scenario`; the source serves `signals` only | Authors, by hand; `extra="forbid"` catches typos |
| `IncidentState` | design §5 plus `opened_at` | Nodes, CLI | Nodes through LangGraph; `evidence` and `actions` are `operator.add` reducers |
| Audit log, checkpoints | none in this increment | | |

`Scenario` shape (every timestamp ISO 8601 with offset; every id matches `^[a-z0-9_-]+$`):

```
id, service, symptoms[1..], incident_time,
signals: { logs: [{id, timestamp, level, message, service}],
           metrics: [{id, name, unit, points: [{at, value}]}],
           deployments: [{id, version, previous_version, deployed_at, author, diff_summary}],
           dependencies: [{id, name, status: healthy|degraded|down, detail}],
           config: {string: scalar} },
ground_truth: {root_cause, correct_action}, required_evidence: [evidence id],
wrong_actions: [action], expected_outcome: remediate|escalate|loop,
post_action_world: {action: {metrics?: [...], logs?: [...]}},
scripted_outputs: {triage: <Triage json>, rca: <RcaOutput json>}
```

`required_evidence` holds evidence ids, not kind names as in the design §6 example (D15).
`author` on a deployment is a synthetic role such as `ci-bot`, never a name (profile control 1).

### Interfaces

`EvidenceSource` (design §6, verbatim signatures): `get_logs(service, window, query) ->
list[LogLine]`, `get_metrics(service, names, window) -> list[Series]`,
`get_deployments(service, window) -> list[Deployment]`, `get_config(service) -> dict`,
`get_dependency_health(service) -> list[DependencyStatus]`, `apply(action, idempotency_key) ->
ActionResult`, `ledger() -> list[AppliedAction]`. `ActionResult` is `{ok, applied,
idempotency_key, detail}`; a refusal is `ok=false` with a sentence in `detail`, never an exception.

`ScriptedChatModel(script: list[BaseModel | dict | str])`: `.calls: list[list[BaseMessage]]`
records prompts; `with_structured_output(schema, *, method="json_schema", include_raw=False)`
returns a runnable producing `schema`; `include_raw=True` raises `NotImplementedError`.

`build_graph(source: EvidenceSource, model: BaseChatModel, checkpointer=None) -> CompiledStateGraph`.

CLI: `agentops run --scenario <id> --model {scripted|anthropic} [--scenarios-dir PATH]`
(`--scenarios-dir` defaults to `./scenarios`). Exit 0 with a plain-text report (incident id,
status, hypotheses with scores, evidence count, root-cause label and explanation, remediation
action, target and risk, a line saying approval and execution arrive in increment 3). Exit 1 for
a usage problem (unknown scenario, directory missing, `anthropic` not yet available), exit 2 when
the run raises after retries; both print one sentence to stderr naming the cause.

CI workflow: one job `checks` on `ubuntu-latest`, `permissions: contents: read`, env
`MIN_TESTS: "30"`, `python -m venv .venv` then every step through `.venv/bin/python -m ...`
(D6), actions `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1` (with
`persist-credentials: false`) and
`actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0` (SHAs confirmed with
`gh api repos/<r>/git/ref/tags/<tag>` on 2026-09-30; both tags are lightweight and point at
commits).

### Security and privacy

No personal data, no secrets, no network in tests (design §17). Baseline items that apply:

- New third-party imports pinned exact: R2. Versions confirmed against PyPI on 2026-09-30 by
  `pip index versions`; the builder re-runs that command on the day and takes a newer release if
  one exists (request).
- `.gitignore` covers `.env*` as a pattern: confirmed in the committed file; `git ls-files`
  shows no `.env` file. The baseline "confirm with git ls-files" line is a step in task 1.
- CI runs lint, typecheck, full tests, dependency audit, with no secrets and read-only token: R16.
  Actions SHA-pinned and a test asserts it: E31. Dependabot keeps pins moving: R18.
- Untrusted content: evidence payloads enter the prompt inside a delimited block the system
  prompt labels as data; the evidence node strips the delimiter characters from payloads (R23)
  so a log line cannot close the block; the test for R14 and R23 proves the hidden scenario
  sections never reach the model and the delimiters survive a hostile line. Scenario 8 (full
  log injection) is increment 2. Tests clear the LangSmith tracing variables so no prompt leaves
  the machine (plan builder notes).
- `Approval.operator` is a role literal (R6), the compliance mapper's control 1.
- RLS, policies, functions, storage buckets, free-text tables, admin capability: not applicable,
  no database.

### Failure modes

| Dependency | Slow or down | Wrong | Handling |
|---|---|---|---|
| PyPI at install (CI and local) | Install step fails, CI red | A pin no longer exists | Re-run; the builder confirms pins with `pip index versions` before committing |
| `pip-audit` | Audit step fails, CI red | A new advisory on a pinned package | Fix by bumping the pin; if no fix exists, record id and reason in `docs/ci.md` and pass `--ignore-vuln <id>` in the workflow (design §13) |
| `pyright` first run (downloads its node runtime, believed) | Typecheck step fails | n/a | Re-run; nothing cached is relied on |
| Scenario file | n/a | Unknown key, missing section, bad timestamp | `load_scenario` raises `ValidationError`; CLI exits 1 naming the file and field |
| Scripted model | n/a | Script exhausted, malformed entry, uncited output | `ScriptExhausted` and `ValidationError` surface; `CitationError` retried twice then surfaces; CLI exits 2 |
| GitHub Actions SHA | Workflow fails at checkout | Wrong SHA for the tag | Only Dependabot or an owner edit changes it; `docs/ci.md` carries the `gh api` command to re-resolve |
| Git hooks not enabled locally | Nothing runs on push | n/a | CI is the gate; the hook is a convenience |

No node makes a network call in this increment, so "model slow or down" has no surface until
increment 2.

### Observability

`actions` in state records every model and source call with latency and a success flag (design
§12). The CLI prints the counts. No logging framework, no LangSmith yet. A failed run prints the
exception class and message on stderr with exit 2; a usage error prints one sentence with exit 1.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Fake model in `tests/fakes.py` as design §13 says, CLI imports from `tests/` | The CLI would depend on the test tree; `pip install .` without tests would break the demo command. ADR 0001 |
| Scripted answers in `tests/fixtures/` instead of the scenario file | The CLI would need a second lookup keyed by scenario id; two files drift. The scenario already holds hidden sections (ground truth) the source never serves, and the script joins them. ADR 0001 |
| Pin the whole design §13 stack now | pip-audit and Dependabot churn on packages nothing imports; each pin is added in the increment that imports it (D4) |
| Rely on LangGraph's default `retry_on` | `ValidationError` is a `ValueError` and would not be retried (believed); a silent non-retry would make "retried by the graph, not by prompt engineering" (design §4) false. ADR 0002 |
| Run CI steps with the runner's Python, no venv | The profile commands, `CONTRIBUTING.md` and the pyright config all name `.venv`; one install shape for humans and CI (D6, ADR 0003) |

## Decisions

- [ADR 0001: Ship the scripted chat model in the package and its answers in the scenario file](./adr/0001-scripted-model-in-package-and-scenario-file.md)
- [ADR 0002: Enforce citations in code with explicit retry classes](./adr/0002-citations-enforced-in-code-with-explicit-retry.md)
- [ADR 0003: CI mirrors the profile's venv install and asserts a test-count floor](./adr/0003-ci-mirrors-the-venv-install-with-a-test-floor.md)

The full decision table with recommendations is in [brief.md](./brief.md), D1 to D17.

## Open questions

None left open. Every question became a decision row in the brief with a recommendation taken.

## Constraint audit

Filled by the constraint auditor. Severity: high blocks G2.

Audit result: pass

Regimes walked: none (profile `compliance.regimes: []`); the profile's `controls_for_later_phases` were checked instead. Database checklist lines (RLS, policies, functions, buckets, free-text tables, admin surface): not applicable, no database (confirmed by reading this spec's Data section and the profile). Spec claim verified: `.gitignore` contains `.env*`, `.venv/`, `*.db`, `audit/` (confirmed by reading the file); whether it is tracked on `main` is believed, not verified by this auditor (no shell available), and task 1 step 6 checks it with `git ls-files`.

| Severity | Finding | Requirement affected | Resolution |
|----------|---------|----------------------|------------|
| medium | The brief's Outcome promises "Every profile command changes from believed to confirmed", but no requirement or plan task edits `.workhorse/profile.yml`, and that file is a sensitive path not named in D1. The same edit is needed for two profile facts this change makes false: the style note "use the scripted fake chat model in tests/fakes.py" (D2 moves it to `src/agentops/fakes.py`), and the `format` command, which writes, while R19 and E34 run it as `--check`. Proposed as brief decision D16 | brief Outcome; R11; R19; plan Files table | decision D16: R22, E3, plan task 7 step 4 |
| low | Only direct dependencies are pinned; transitive packages pulled by langgraph and langchain-core float at install time, with no lockfile or hashes. Known and deferred by the design (constraints.md "Known debt", design §19 D10) | R2 | accepted: known debt in constraints.md; builder note (docs/ci.md names it) |
| low | `security_audit` runs `pip_audit -r requirements.txt` only. The dev tools that execute inside CI (pytest, ruff, pyright, pip-audit, pyyaml) and the `setuptools==84.0.0` build backend are never audited | R16, R19 | builder note: recorded in `docs/ci.md` as deferred |
| low | The `pyright` PyPI wrapper is believed to download a Node runtime and the npm pyright package on first run. That is a network fetch of executable code in the CI path that no pin in `requirements-dev.txt` controls | R16; Failure modes row "pyright first run" | builder note: recorded in `docs/ci.md`; plan risk row |
| low | langchain-core is believed to enable LangSmith tracing from the `LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` environment variables. A developer shell with those set would send test and demo prompts to LangSmith, contradicting "open no socket" and making E26 depend on the environment. Tests should clear those variables, for example in `tests/conftest.py` | R11, R14 (E26) | builder note: task 6 step 1, `conftest.py` autouse fixture |
| low | Evidence is rendered as JSON between `<<<untrusted evidence>>>` and `<<<end>>>`. JSON encoding does not escape `<<<end>>>`, so a log message containing that text closes the block early. The renderer should reject or escape the delimiter inside payloads; scenario 8 tests this in increment 2 | R14; plan task 6 step 3 | decision D17: R23, E26 extended, plan task 6 step 3 |
| low | `actions/checkout` keeps `GITHUB_TOKEN` in `.git/config` for later steps by default. Set `persist-credentials: false`. E31 could also assert that `pull_request_target` is absent before the repository goes public | R16 (E31) | builder note: R16 amended, E30 and E31 assert both, plan task 4 step 3 |
| low | R20 (green Actions run) has no machine acceptance check. The spec says so openly; the verifier should list it under "Not verified" until the owner reads the run | R20 | builder note: plan verification plan says the verifier lists R20 under "Not verified" |
| low | No `SECURITY.md` disclosure contact. Not needed while the repository is private; add one before it goes public in increment 5 | R3 | builder note: deferred to increment 5, recorded in `docs/ci.md` |

## Response to audit

One round, no re-design. "No requirement or plan task edits `.workhorse/profile.yml`": added R22,
decision D16, E3 extended, task 7 edits the profile as a sensitive path. "A log message
containing `<<<end>>>` closes the block early": added R23, decision D17, E26 extended with a
hostile log line, task 6 step 3 neutralises the delimiter characters in the evidence node.
"Set `persist-credentials: false`" and "`pull_request_target` absent": R16 amended, E30 and E31
assert them. "Tests should clear those variables": `conftest.py` fixture in task 6. Transitive
pins, dev-tool audit, pyright runtime fetch, `SECURITY.md`: recorded in `docs/ci.md` by task 1
as accepted or deferred items, so the next reader finds them without this folder.
