# Plan: wire the live Anthropic model path

Change id: `2026-10-05-wire-live-anthropic-model-path`
Spec: [spec.md](./spec.md)
Branch: `wh/2026-10-05-wire-live-anthropic-model-path`
Worktree: the conductor's worktree for this change (one per wave task)

## Approach

Wave 1 lays four independent foundations: the accounting path through `ask_model` and the fake,
the price table, the models module with its pins, and the test-suite network guard. Wave 2
threads them through the graph, the baseline, the judge, the outcome and the live workflow. Wave
3 wires the commands (spend guard, `--dev`, `--yes`, table columns). Wave 4 writes the documents
against the final CLI. No task makes a live call; the smoke test (E27) is skipped on every
builder machine because `AGENTOPS_TEST_ANTHROPIC_API_KEY` is unset there. Ids E2 and E36 are
unused (merged into E1 and E7 at revision).

## Files

| Action | Path | Purpose |
|--------|------|---------|
| create | `src/agentops/models.py`, `src/agentops/pricing.py` | Roles, settings, key check, factories (ADR 0001); price table, `cost_usd`, `estimated_cost` |
| create | `.github/workflows/live-smoke.yml` | Dispatch-only smoke job (ADR 0003); ask-first path |
| create | `docs/hosted-config.md` | Test workspace record (D11): names only, values filled by the owner |
| create | `tests/test_models.py`, `tests/test_pricing.py`, `tests/test_model_calls.py`, `tests/test_network_guard.py`, `tests/test_live_smoke.py`, `tests/test_live_workflow.py` | New cases |
| modify | `requirements.txt`, `pyproject.toml` | Two pins; the `live_smoke` marker (both ask-first paths) |
| modify | `src/agentops/state.py`, `src/agentops/graph.py` | `Action` cache and model fields; models by role (both tier-2 floor paths) |
| modify | `src/agentops/nodes/__init__.py`, `nodes/triage.py`, `nodes/rca.py`, `nodes/postmortem.py`, `nodes/escalate.py`, `src/agentops/fakes.py` | `ask_model` with usage and the two-block system message; stable/fence split; fake `include_raw` and usage stamps |
| modify | `src/agentops/baseline.py`, `src/agentops/evals/judge.py`, `src/agentops/evals/outcome.py` | Middleware, usage by model, structured judge, `TokenUsage` |
| modify | `src/agentops/evals/run.py`, `src/agentops/evals/commands.py`, `src/agentops/cli.py` | Factory, judge column, cost columns, spend guard, `--dev`, `--yes`, key exit |
| modify | `tests/conftest.py`, `tests/test_tracing.py`, `tests/test_fakes.py`, `tests/test_verify_postmortem.py`, `tests/test_graph.py`, `tests/test_baseline.py`, `tests/test_evaluators.py`, `tests/test_eval_run.py`, `tests/test_eval_cli.py`, `tests/test_cli.py`, `tests/test_cli_lifecycle.py`, `tests/test_requirements.py`, `tests/test_repo_documents.py` | Cases and the three stub assertions the new behaviour replaces |
| modify | `README.md`, `CLAUDE.md`, `docs/ci.md`, `docs/sdlc/codebase-map.md` | Commands, variables, costs, the second workflow, transitive names |

Not edited: `ci.yml`, `src/agentops/actions/**`, `nodes/approval.py`, `nodes/execute.py`,
`runbooks/**`, `evals/**` data, `.gitignore`, `.workhorse/profile.yml`, any `.env*` file.

## Tasks

### Task 1: usage-aware `ask_model`, two-block system prompt, fake `include_raw`

- Requirement(s): R2, R5, R6
- Files: `src/agentops/state.py`, `src/agentops/nodes/__init__.py`, `src/agentops/nodes/triage.py`, `src/agentops/nodes/rca.py`, `src/agentops/nodes/postmortem.py`, `src/agentops/nodes/escalate.py`, `src/agentops/fakes.py`, `tests/test_model_calls.py`, `tests/test_fakes.py`, `tests/test_verify_postmortem.py`
- Parallel: yes
- Steps:
  1. Write failing tests in `tests/test_model_calls.py`: E4 (kwargs recorded, parsed returned), E5 (bad raw raises `ValidationError`, and `ValidationError in MODEL_NODE_RETRY.retry_on`), E6 and E7 (block shapes per node; block 1 byte-identical across two nonces), E8 and E9 (`usage_action` shapes; the no-metadata reply yields zeros and the configured id), E32 (3 failed attempts, 3 Actions), E35 (legacy dicts load), E39 (the scenario 8 injected log line appears only between the fence markers; block 1 text non-empty; the absence check runs against a non-empty string); in `tests/test_fakes.py`: E10 (full-run actions carry tokens and `scripted`; `include_raw=True` reply shape).
  2. Run them; confirm each fails on a missing attribute or `NotImplementedError`, not on a typo.
  3. Add `cache_read_tokens`, `cache_write_tokens`, `model` to `Action`. Implement `usage_action(raw, latency_ms, configured_model)` and the new `ask_model` (ADR 0002): `system_blocks(stable, fence_sentence | None)`, `include_raw=True`, validate from `raw.text` when `parsed` is not an instance; `ask_model` passes the client's configured id (`model.model` on `ChatAnthropic`, believed attribute name, builder confirms in the 1.7.5 source; the fake exposes `model = "scripted"`). Split each node's prompt into `<NODE>_INSTRUCTIONS` (stable, ending with `UNTRUSTED_DATA_RULE`) and the fence sentence passed separately; keep `*_system_prompt(nonce)` helpers returning the joined text for tests that read it.
  4. In `fakes.py`: honour `include_raw=True` (return `raw` as an `AIMessage` whose content is the JSON text, `parsed` or `None` with `parsing_error`), stamp `usage_metadata` (input `ceil(prompt chars / 4)`, output `ceil(reply chars / 4)`, details zeros) and `response_metadata={"model": "scripted"}` on every reply, including tool-call replies; `prompt_lines` must read text from block lists too (E39 is the check that it does).
  5. Update the one assertion in `tests/test_verify_postmortem.py` (line 592) that the system content is a `str`: read the text of the blocks instead (contract change from R5, ADR 0004; say so in the commit body).
  6. Green; full suite; `ruff format`.
  7. Commit: `feat(nodes): record token usage per model call and cache the stable system block`
- Done when: E4 to E10, E32, E35, E39 pass; every existing prompt and injection test passes unchanged except the one assertion in step 5.

### Task 2: price table

- Requirement(s): R7
- Files: `src/agentops/pricing.py`, `tests/test_pricing.py`
- Parallel: yes
- Steps:
  1. Write failing tests E11, E12, E13.
  2. Confirm `ImportError`.
  3. Implement `ModelPrice` (frozen dataclass, USD per million: `input`, `output`, `cache_write`, `cache_read`), `Unpriced` (frozen dataclass, `model_id`), `PRICES` for `claude-sonnet-5-5` and `claude-haiku-4-5-20251001`, `PRICES_AS_OF`, `PRICES_SOURCE`, `cost_usd(usage_by_model) -> float | Unpriced`, `estimated_cost(model_ids, runs_per_system, judge_calls) -> float | Unpriced` (priced role by role: each graph run is `ESTIMATED_CALLS_PER_RUN["graph"]` calls split evenly over the four graph roles at each role's own id; the baseline at the baseline id; the judge at the judge id; the first unpriced id short-circuits to `Unpriced`), and the constants `ESTIMATED_CALLS_PER_RUN = {"graph": 12, "baseline": 12}`, `ESTIMATED_INPUT_TOKENS_PER_CALL = 6_000`, `ESTIMATED_OUTPUT_TOKENS_PER_CALL = 1_000`, `ESTIMATED_JUDGE_TOKENS = (2_000, 100)`, each commented "design §11 upper bound, believed; replace with the measured mean after the first paid run".
  4. Read the prices from the Anthropic pricing page on build day, write them with the date in `PRICES_AS_OF` and the page URL in `PRICES_SOURCE`, and say in the module comment that they were transcribed, not computed. Never guess a price; if the page cannot be read, leave the task red and report it.
  5. Green; full suite. Commit: `feat(pricing): price table and measured cost per run`
- Done when: E11 to E13 pass and the module has no runtime import from `agentops.evals` (it is imported by `outcome.py` later).

### Task 3: models module and pins

- Requirement(s): R1, R3, R4, R15, R17
- Files: `src/agentops/models.py`, `requirements.txt`, `tests/test_models.py`, `tests/test_requirements.py`, `tests/test_tracing.py`
- Parallel: yes
- Steps:
  1. Write failing tests: E1 and E3 in `tests/test_models.py` (inject the constructor through a `build_chat_model` parameter or `monkeypatch` of `models.chat_model_class`; E1 covers default and dev ids, explicit `base_url`, `timeout`, `max_retries` with `ANTHROPIC_BASE_URL` set in the environ, and a sentinel scenario never read); E29 in `tests/test_tracing.py` (allowlist `{"models.py": {"langchain_anthropic", "anthropic"}}`, function-level only, no `dotenv`/`open(` in `models.py`, `sys.modules` check after `import agentops.cli`); E33 in `tests/test_models.py` (blank/whitespace key, no key in repr); E30's two pin lines in `tests/test_requirements.py`.
  2. Confirm failures.
  3. Confirm the pins on PyPI on build day (`pip index versions langchain-anthropic anthropic`; on 2026-10-05 they were 1.7.5 and 1.11.0) and that langchain-anthropic's `Requires-Dist` admits `langchain-core==1.6.6` and the chosen `anthropic`; add the two lines with `# MIT` after `langchain==1.4.3`; run the profile `install` command; then run `pip show anthropic` and compare `Requires` with the spec's list (`httpx2`, `jiter`, `anyio`, `docstring-parser`, `sniffio`, `typing-extensions`, confirmed from the wheel on 2026-10-05; `httpx2` is pydantic's package, confirmed on PyPI). Any name not in that list is checked on PyPI before it goes in the commit body; Task 9 copies the list into `docs/ci.md`.
  4. Implement `models.py`: `ModelRole`, `ROLES`, `ModelSettings.from_environment(dev, environ)` (`AGENTOPS_MODEL_<ROLE>` overrides), `MissingApiKeyError`, `require_api_key(environ)` (strip; blank is missing; message names the variable only), `ModelFactory` protocol (`label`, `runs_judge`, `graph_models(scenario)`, `baseline_model(scenario)`, `judge_model()`, `baseline_middleware()`), `ScriptedModels`, `LiveModels` (lazy import inside `__init__`; `ChatAnthropic(model=..., api_key=SecretStr(key), base_url="https://api.anthropic.com", timeout=120, max_retries=2, max_tokens=4096)`; `scenario` ignored; `baseline_middleware` returns `(AnthropicPromptCachingMiddleware(ttl="5m"),)`), `model_factory(model_name, dev, environ)`.
  5. Green; full suite. Commit: `feat(models): live Anthropic models per role, key from the environment only`
- Done when: E1, E3, E29, E33 and the pin lines of E30 pass; `pip install` exits 0; `python -c "import agentops.cli"` imports no `langchain_anthropic`.

### Task 4: network guard

- Requirement(s): R12, R17
- Files: `tests/conftest.py`, `tests/test_network_guard.py`, `pyproject.toml`
- Parallel: yes
- Steps:
  1. Write failing tests E23, E24 and E34 (lookalike hosts, `connect_ex`, and a `live_smoke`-marked test in the file that still sees the guard raise because `AGENTOPS_TEST_ANTHROPIC_API_KEY` is unset).
  2. Confirm they fail (no `NetworkBlockedInTests`).
  3. Add to `conftest.py` an autouse fixture `offline` that lifts itself only when the test carries `live_smoke` AND `AGENTOPS_TEST_ANTHROPIC_API_KEY` is non-empty; otherwise it deletes `ANTHROPIC_API_KEY` and patches `socket.getaddrinfo`, `socket.socket.connect` and `socket.socket.connect_ex` to raise `NetworkBlockedInTests(host)` for any host or address not in `{"localhost", "127.0.0.1", "::1"}` (loopback stays open: Python's `socketpair` on Windows connects to loopback). Register `live_smoke` under `[tool.pytest.ini_options] markers` with `--strict-markers`.
  4. Green; full suite on Windows and confirm no test needed loopback beyond `socketpair`. Commit: `test: block non-loopback connections and the app key in every test`
- Done when: E23, E24, E34 pass and the full suite is green with the guard on.

### Task 5: models by role in the graph, baseline usage and middleware

- Requirement(s): R1, R5, R10
- Files: `src/agentops/graph.py`, `src/agentops/baseline.py`, `tests/test_graph.py`, `tests/test_baseline.py`
- Parallel: yes (wave 2)
- Steps:
  1. Write failing tests: a graph test that `build_graph` with a `{role: model}` mapping routes each node to its model (use four distinct `ScriptedChatModel`s and assert which recorded calls); E19 and E20 in `tests/test_baseline.py`.
  2. Confirm failures.
  3. `graph.py`: `models_by_role(model) -> dict[ModelRole, BaseChatModel]` (a single model fans out to every role); nodes take their role's model. `baseline.py`: `run_baseline(..., middleware=())` passed to `create_agent`; `BaselineRun.usage_by_model` (import `TokenUsage` from `agentops.evals.outcome`, already imported there for `ApplyAttempt`); sum by `response_metadata.get("model", <configured id>)`.
  4. Green; full suite. Commit: `feat(graph): one model per role; baseline records usage by model and accepts middleware`
- Done when: the graph routing test, E19 and E20 pass; `build_graph(source, single_model, ...)` callers are untouched.

### Task 6: structured judge and outcome usage

- Requirement(s): R7, R10, R11
- Files: `src/agentops/evals/judge.py`, `src/agentops/evals/outcome.py`, `tests/test_evaluators.py`
- Parallel: yes (wave 2)
- Steps:
  1. Write failing tests: E21; an outcome test that `outcome_from_state` fills `usage_by_model` from `actions` and `RunOutcome` still validates without the field.
  2. Confirm failures.
  3. `outcome.py`: `TokenUsage` (StrictModel, four non-negative ints, `__add__`), `RunOutcome.usage_by_model: dict[str, TokenUsage] = {}`, `usage_by_model_of(actions)`; `judge.py`: `explanation_quality` through `ask_model(model, JudgeVerdict, JUDGE_RUBRIC, human)` catching `ValidationError` as `judge output invalid`, returning the `Action` to the caller via a new `judge_with_usage(...) -> tuple[dict, Action | None]` that `explanation_quality` wraps.
  4. Green; full suite. Commit: `feat(evals): judge through structured output; usage by model in the outcome`
- Done when: E21 and the outcome test pass; `DETERMINISTIC_EVALUATORS` unchanged.

### Task 7: live smoke test and dispatch-only workflow (sensitive path `.github/workflows/**`)

- Requirement(s): R13, R14
- Files: `.github/workflows/live-smoke.yml`, `tests/test_live_smoke.py`, `tests/test_live_workflow.py`, `docs/ci.md`
- Parallel: yes (wave 2)
- Steps:
  1. Write failing tests E26 and E28 (E28 reuses `SHA_PINNED_ACTION` and `USES_LINE` by importing them from `tests/test_ci_workflow.py`).
  2. Confirm failures (file missing).
  3. Write the workflow per ADR 0003, pinning `actions/checkout` and `actions/setup-python` to the same SHAs `ci.yml` uses; env maps `AGENTOPS_TEST_ANTHROPIC_API_KEY: ${{ secrets.AGENTOPS_TEST_ANTHROPIC_API_KEY }}`; the key-check step prints `AGENTOPS_TEST_ANTHROPIC_API_KEY is not set; add it under Settings > Secrets and variables > Actions` and exits 1. Write `tests/test_live_smoke.py`: `live_smoke_key(environ)`, the gate tests (E26), and the `live_smoke` test (E27) that `monkeypatch.setenv("ANTHROPIC_API_KEY", key)`, builds `LiveModels(ModelSettings.from_environment(dev=True, ...))`, and runs one `ask_model(..., Triage, ...)` with a two-symptom prompt. Add a `docs/ci.md` section "The live smoke job" saying the secret is a test-workspace key (D11) and pointing at `docs/hosted-config.md`.
  4. Green locally (E27 skipped). Commit: `ci: dispatch-only live smoke job reading AGENTOPS_TEST_* only`
- Done when: E26 and E28 pass; `pytest -q` reports exactly one skipped test from this file; `git diff main -- .github/workflows/ci.yml` is empty.

### Task 8: commands: spend guard, `--dev`, `--yes`, cost columns, judge column

- Requirement(s): R1, R4, R8, R9, R11
- Files: `src/agentops/evals/run.py`, `src/agentops/evals/commands.py`, `src/agentops/cli.py`, `tests/test_eval_run.py`, `tests/test_eval_cli.py`, `tests/test_cli.py`, `tests/test_cli_lifecycle.py`
- Parallel: no (wave 3; depends on tasks 1 to 6)
- Steps:
  1. Write failing tests: E14 to E18, E22, E31 (SDK-style exception from a stub inside a node: no retry, exit 2, type in stderr, no key in output; in `test_cli.py` and `test_eval_cli.py`), E37 (estimate equals `estimated_cost`; the mixed-id variant rises by the rca share) and E38 (unpriced id: `estimated cost: unknown (unpriced: claude-x)`, no `$`, no `KeyError`, `--yes` still required) in `tests/test_eval_cli.py` and `tests/test_eval_run.py`; E25 across the three CLI test files, replacing the three `not wired yet` assertions (`tests/test_cli.py:105`, `tests/test_cli_lifecycle.py:196`, `tests/test_eval_cli.py:242`) with the key-missing contract (a requirement change, named in the commit body).
  2. Confirm failures.
  3. `run.py`: `run_split(..., models: ModelFactory)`; `run_graph_once` and `run_baseline_once` take their models and middleware from it; judge rows when `models.runs_judge`; `render_table` columns and the `judge cost:` line; `rows_as_json` carries usage and cost. `commands.py`: `--dev`, `--yes`; order: model name check, repetitions, systems, tracing, scenarios, checksums, split, then `model_factory` (exits 1 on a missing key), then the spend guard (E16 text; `Unpriced` prints the `unknown` line and never a figure), then runs. `cli.py`: `--dev` on the four thread commands; `check_model_is_available` becomes `model_factory`; `ThreadRun.start` takes the factory. `AUTO_APPROVAL` stays where it is in `run.py`, unchanged.
  4. Green; full suite. Commit: `feat(cli): live model path with a spend guard, --dev and measured cost columns`
- Done when: E14 to E18, E22, E25, E31, E37, E38 pass; `agentops eval --model scripted` output is unchanged except the new columns.

### Task 9: documents

- Requirement(s): R16
- Files: `README.md`, `CLAUDE.md`, `docs/ci.md`, `docs/hosted-config.md`, `docs/sdlc/codebase-map.md`, `tests/test_repo_documents.py`
- Parallel: no (wave 4)
- Steps:
  1. Write the failing document assertions of E30 in `tests/test_repo_documents.py` (names, the two README sentences, the hosted-config headings).
  2. Confirm failure.
  3. README: status line, the live command forms, a "The live model" subsection under Measuring it (variables, `--dev`, the guard, the cost columns, the price date, that nothing in tests or CI's default job calls a model, how to run the smoke test, that `AGENTOPS_TEST_ANTHROPIC_API_KEY` comes from a separate Anthropic workspace with a monthly spend limit and the app key must not be reused, and that reject reasons and edited remediations typed at approval are sent to the model provider on a live run). `CLAUDE.md` commands paragraph (replace the "exits 1: not wired yet" sentence). Create `docs/hosted-config.md` with one section "Anthropic test workspace" and the fields `AGENTOPS_TEST_ANTHROPIC_API_KEY`, workspace, spend limit, owner, rotation, each "to be recorded by the owner"; names only, never a value. `docs/ci.md`: the transitive names from Task 3's `pip show anthropic`. Codebase map: `models.py` and `pricing.py` present, `live-smoke.yml` and `hosted-config.md` listed, "Not yet present" updated.
  4. Green. Commit: `docs: live model commands, variables, the cost table and the test workspace record`
- Done when: E30 passes and no document still says the live model is not wired.

## Waves

| Wave | Tasks | Files (disjoint within the wave) |
|------|-------|----------------------------------|
| 1 | T1, T2, T3, T4 | nodes/fakes/state + 3 tests; pricing + test; models/requirements + 3 tests; conftest/pyproject + 1 test |
| 2 | T5, T6, T7 | graph/baseline + 2 tests; judge/outcome + 1 test; workflow/ci.md + 2 tests |
| 3 | T8 | run/commands/cli + 4 tests |
| 4 | T9 | README/CLAUDE/ci.md/hosted-config/codebase-map + 1 test |

`build.max_parallel` is 4; wave 1 uses all four.

## Builder notes

- Model ids and the per-role override: `AGENTOPS_MODEL_TRIAGE`, `_RCA`, `_POSTMORTEM`, `_ESCALATE`, `_BASELINE`, `_JUDGE`; unknown names are ignored; a blank value means "not set".
- `TokenUsage` lives in `evals/outcome.py`; `pricing.py` takes a `Mapping[str, TokenUsage]` typed through `typing.Protocol` or a `TYPE_CHECKING` import so it never imports the evals package at runtime.
- `usage_action` reads `raw.usage_metadata` (may be `None`) and `raw.response_metadata.get("model")`; when either is absent, tokens are 0 and `model` is the configured id (R6, E9). Never `None` on a model call.
- `LiveModels`: `base_url`, `timeout` and `max_retries` are explicit (parameter names confirmed in the 1.7.5 `chat_models.py`: aliases of `anthropic_api_url`, `default_request_timeout`, `max_retries`); the SDK's own `ANTHROPIC_BASE_URL` read (`_client.py` line 234, 1.11.0) is bypassed by the kwarg. Do not read the scenario.
- The fake's usage stamp is deterministic (`ceil(len / 4)`). Do not raise `MIN_TESTS`: `ci.yml` stays byte-identical (R14). Never put a real key in a test or a commit; tests use the literal `fake`. Do not edit `.workhorse/profile.yml` (the `retention_notes` addition is the owner's).

## Verification plan

Profile commands: `typecheck`, `lint`, `format`, `test`, `security_audit`, plus `install` after
the pin change. Evals: E1, E3 to E26, E28 to E35, E37 to E39 run inside `pytest -q`; E27 is
listed under "Not verified: needs AGENTOPS_TEST_ANTHROPIC_API_KEY, owner runs it". Extra checks
the verifier runs and quotes: `git diff main -- .github/workflows/ci.yml` (empty);
`git diff main -- src/agentops/nodes/approval.py src/agentops/nodes/execute.py src/agentops/actions/ runbooks/`
(empty: the approval, execute and action boundary is untouched); `git ls-files | grep "^\.env"`
(empty); `pip show anthropic` output quoted next to the `docs/ci.md` list. The human sees the test
count, the new eval table with `n/a (unpriced: scripted)`, and the spend guard output from E16's
and E38's captured stdout.

## Rollback

Local CLI only; nothing deployed. Revert the merge commit on `main` (the owner's action). A
checkpoint file written by the new code holds the new `Action` fields, which the reverted
`extra="forbid"` model rejects, so delete `agentops.sqlite` from the live trial after a revert.
No migration, no data to restore.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A price transcribed wrongly | Low | Wrong cost printed | `as_of` and `source` are printed; owner checks at Ship (brief D5); `cost_usd` is never estimated from text |
| `response_metadata["model"]` absent on live replies | Low | Cost attributed to the configured id | That is the designed fallback (R6, E9); the table still shows it |
| Model ids wrong (constraints Q5, believed) | Medium | First live call fails with a 404 naming the id | `AGENTOPS_MODEL_<ROLE>` override needs no code change; an unpriced override prints `unknown` (D10) |
| Network guard breaks a Windows-only loopback use | Low | Suite red | Loopback allowed; task 4 runs the suite on Windows |
| The structured-output API rejects a schema shape | Low | Live call 400 | `transform_schema` confirmed to rewrite constraints; the smoke test is the first place it shows, on Haiku |
| Builder stalls on the ask-first paths | Medium | Minutes | The owner approves `requirements.txt`, `pyproject.toml` and `live-smoke.yml` edits at Design (brief D1, D2) |
