# Spec: wire the live Anthropic model path

Change id: `2026-10-05-wire-live-anthropic-model-path`
Intent: the request in `conductor-log.md` (no intent.md was written for this change)
Status: draft
Policy skills applied: wh-agent-rules, wh-security-baseline, wh-readable-code, wh-adr, wh-evals

## Summary

`--model anthropic` stops being a stub: the four model nodes, the ReAct baseline and the judge
run on `ChatAnthropic` built in one new module, `src/agentops/models.py`, with native
`json_schema` structured output, a key read only from the process environment, and every call's
tokens and model id written into `state.actions` so `agentops eval` prints a measured cost per
run. The decision that matters most: nothing in this change makes a live call; the build, the
tests and CI's default job stay keyless and offline, proven by a network guard in `conftest.py`,
and the first paid call is the owner's, behind a spend guard that needs `--yes`.

Every claim below is `confirmed` by reading the repository or the 1.7.5 and 1.11.0 wheels unless
marked `believed`.

## Requirements

| ID | Requirement | Acceptance check | Source |
|----|-------------|------------------|--------|
| R1 | `--model anthropic` SHALL build one `ChatAnthropic` per role (`triage`, `rca`, `postmortem`, `escalate`, `baseline`, `judge`) from `ModelSettings`: default id `claude-sonnet-5-5` for every role; `--dev` on `run`, `approve`, `resume`, `fork` and `eval` sets `claude-haiku-4-5-20251001` for every role; `AGENTOPS_MODEL_<ROLE>` (upper case role) overrides one role. Each client SHALL be built with an explicit `base_url` of `https://api.anthropic.com` (so `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_URL` are ignored), `timeout=120` seconds and `max_retries=2`; `LiveModels` SHALL read nothing from the `scenario` argument. `langgraph dev` keeps the scripted model | A test with a recording constructor sees six models with the expected ids for default, `--dev` and one override, each with the explicit base URL, timeout and retries even when `ANTHROPIC_BASE_URL` is set; the scenario passed is a sentinel the factory never touches | request (1) |
| R2 | Every structured call SHALL use `with_structured_output(schema, method="json_schema", include_raw=True)` and never a forced `tool_choice`; a reply that fails the schema SHALL raise `pydantic.ValidationError` inside the node so `MODEL_NODE_RETRY` retries it | Test with a stub model asserts the kwargs and that a bad raw reply raises `ValidationError` | request (1), design §5, §19 D6, ADR 0002 |
| R3 | `requirements.txt` SHALL pin `langchain-anthropic` and `anthropic` to exact versions with licence comments; the pins SHALL be confirmed on PyPI on build day and SHALL satisfy langchain-anthropic's range for the existing `langchain-core==1.6.6` pin (range `>=1.6.6,<2.0.0`, confirmed from the 1.7.5 wheel) | `tests/test_requirements.py` finds both lines; the profile `install` command exits 0 | request (1), D1 |
| R4 | The key SHALL be read only from `ANTHROPIC_API_KEY` in `os.environ` and passed to the client explicitly; no code path SHALL open a file to find it; when it is missing or blank every live command SHALL exit 1 before any run with one sentence naming the variable and never a value | CLI tests with the variable removed exit 1 with `ANTHROPIC_API_KEY` in stderr and nothing on stdout; a source scan of `models.py` finds no `dotenv` and no `open(` | request (2) |
| R5 | Each node's system message SHALL be two text blocks: stable instructions (with `UNTRUSTED_DATA_RULE`) carrying `cache_control: {"type": "ephemeral"}`, then the nonce-bearing fence sentence; the live baseline SHALL run with `AnthropicPromptCachingMiddleware`; the scripted baseline SHALL run with no middleware | Tests read the blocks the fake recorded: block 1 has `cache_control` and no nonce, block 2 has the nonce; `run_baseline` receives the middleware only from `LiveModels` | request (3), ADR 0004 |
| R6 | Every model call's `Action` SHALL record `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` and `model` from the reply's `usage_metadata` and `response_metadata`; when the reply carries no usage the tokens SHALL be 0, and when it carries no model id the `model` SHALL be the configured id of the client that answered (never `None` on a model call), so cost stays attributable; the fake SHALL stamp deterministic non-zero usage and model `scripted` | A scripted graph run's `actions` all have tokens above zero and model `scripted`; a unit test feeds the ephemeral-key and plain `cache_creation` shapes and gets the right write count, and a reply with no metadata yields zeros and the configured id | request (4), ADR 0002 |
| R7 | `src/agentops/pricing.py` SHALL hold one price table: per model id, USD per million tokens for input, output, cache write and cache read, plus `as_of` (ISO date) and `source` (the Anthropic pricing page URL); `cost_usd(usage_by_model)` SHALL return the cost, charging `input - cache_read - cache_write` at the input price, or `None` naming the first unpriced model id | Unit tests: a hand-computed cost matches to the cent; `scripted` returns `None`; the table names both default ids with positive prices and a valid date | request (4), D5 |
| R8 | The eval table SHALL add per system the mean/min/max of input, output, cache-read and cache-write tokens and of `cost_usd`, printing `n/a (unpriced: <id>)` when any run's model is unpriced; `--json` rows SHALL carry `usage_by_model` and `cost_usd` | Scripted eval CLI test sees `n/a (unpriced: scripted)`; a render test with a priced fake row prints dollars | request (4) |
| R9 | `agentops eval --model anthropic` SHALL, after the usual checks and before any model is built, print the planned run count (`systems × scenarios × repetitions`, plus one judge call per graph run) and the estimated cost from the price table and the per-run token estimate in `pricing.py`, then exit 1 unless `--yes`; the estimate SHALL be computed per role with that role's own id (so a mixed `AGENTOPS_MODEL_<ROLE>` set is priced role by role); when any id in use is unpriced the line SHALL read `estimated cost: unknown (unpriced: <id>)`, never a partial or zero figure, and `--yes` is still required; `--held-out` stays required for the frozen split; `--yes` with `--model scripted` is accepted and changes nothing | CLI test with a fake key and no `--yes`: exit 1, stdout has the count and `$`, the constructor spy was never called; with `AGENTOPS_MODEL_RCA` set to an unpriced id the line reads `unknown (unpriced: <id>)` and holds no `$` | request (5), D10 |
| R10 | The baseline and the judge SHALL take their models from the same `ModelFactory` as the graph; `BaselineRun` SHALL carry `usage_by_model` summed from the replies' `usage_metadata` by `response_metadata["model"]` | Baseline test on the fake shows `usage_by_model == {"scripted": ...}` with the reply count | request (1), (4) |
| R11 | The judge SHALL ask for `JudgeVerdict` through `ask_model` (json_schema); an invalid reply SHALL score 0 with `judge output invalid`; `agentops eval --model anthropic` SHALL run it on every graph row as the column `explanation_quality` and print its cost on its own line; with `--model scripted` the judge SHALL not run and the column is absent | Existing judge tests pass; a run test with a judging fake shows the column and the separate cost line | request (1), D7 |
| R12 | No test SHALL open a connection to a non-loopback address: an autouse fixture in `conftest.py` SHALL make `socket.getaddrinfo`, `socket.socket.connect` and `connect_ex` raise `NetworkBlockedInTests` for any host other than `localhost`, `127.0.0.1` and `::1`; the guard SHALL be lifted only when the test is marked `live_smoke` AND `AGENTOPS_TEST_ANTHROPIC_API_KEY` is non-empty (the marker alone keeps the guard on); the same fixture SHALL remove `ANTHROPIC_API_KEY` from the environment for every guarded test | A self-test asserts the guard raises for `api.anthropic.com`; a marked test with the test variable unset still sees the guard raise; a test asserts `ANTHROPIC_API_KEY` is absent | request (6) |
| R13 | `tests/test_live_smoke.py` SHALL hold one test marked `live_smoke` that skips unless `AGENTOPS_TEST_ANTHROPIC_API_KEY` is non-empty, never reads `ANTHROPIC_API_KEY` as a fallback, and when it runs sets `ANTHROPIC_API_KEY` from the test variable for its own duration, asks `LiveModels` (dev ids) for one `Triage` through `ask_model`, and asserts a `Triage`, tokens above zero and a model id starting `claude-haiku`. The test key SHALL come from a separate Anthropic workspace with a low monthly spend limit, never the app key; its workspace name, limit, owner and rotation (names only) SHALL be recorded in `docs/hosted-config.md` (D11) | Gate tests: unset → skip; `ANTHROPIC_API_KEY` set alone → skip; the marker is registered in `pyproject.toml`; the document test finds the hosted-config section | request (6), D11 |
| R14 | `.github/workflows/live-smoke.yml` SHALL trigger only on `workflow_dispatch`, with `permissions: contents: read`, SHA-pinned actions, only `secrets.AGENTOPS_TEST_*` references, a step that exits 1 naming `AGENTOPS_TEST_ANTHROPIC_API_KEY` and where to set it when blank, the smoke test alone, and `check_test_count.py live.xml 1` so a skipped test fails the job; `ci.yml` SHALL be unchanged | `tests/test_live_workflow.py` parses the file; `git diff main -- .github/workflows/ci.yml` is empty | request (6), ADR 0003 |
| R15 | Only `src/agentops/models.py` SHALL import `langchain_anthropic` or `anthropic`, and only inside the live factory function; `langsmith` stays forbidden in every module | The scan test's allowlist is exactly `{"models.py"}` for the two roots | design §13, ADR 0001 |
| R16 | README (status, commands, Measuring it, Tracing), `CLAUDE.md` commands, `docs/ci.md` and `docs/sdlc/codebase-map.md` SHALL describe `--model anthropic`, `--dev`, `--yes`, the three variable names (`ANTHROPIC_API_KEY`, `AGENTOPS_TEST_ANTHROPIC_API_KEY`, `AGENTOPS_MODEL_<ROLE>`), the cost columns and the price table's date; the README SHALL say that the test key must come from a separate workspace and the app key must not be reused, and that reject reasons and edited remediations typed at the approval step are sent to the model provider on a live run; a new committed `docs/hosted-config.md` SHALL hold a section for the test workspace (name, monthly limit, key owner, rotation; names only, values filled by the owner); no `.env*` file is created | `tests/test_repo_documents.py` finds the names, the two README sentences and the hosted-config section headings; `git ls-files` lists no `.env*` | request (7), D11 |
| R17 | Non-functional: with `--model scripted` the full suite SHALL run with no connection attempt and no key; `import agentops.cli` SHALL not import `langchain_anthropic` | Network guard self-test; `sys.modules` check after importing the CLI | request (6) |

## Design

### Architecture

The graph keeps its shape (`src/agentops/graph.py`); `build_graph` and `build_state_graph`
accept `model: BaseChatModel | Mapping[ModelRole, BaseChatModel]` and resolve a mapping per
node, so every existing caller that passes one model is unchanged. New module
`src/agentops/models.py` (ADR 0001): `ModelRole`, `ModelSettings` (frozen dataclass:
`model_ids: dict[ModelRole, str]`, built by `ModelSettings.from_environment(dev, environ)`),
`require_api_key(environ) -> str` (raises `MissingApiKeyError`), the `ModelFactory` protocol
(`graph_models(scenario)`, `baseline_model(scenario)`, `judge_model()`, `baseline_middleware()`,
`label`), `ScriptedModels` over `agentops.fakes`, and `LiveModels(settings, api_key)` whose
constructor imports `langchain_anthropic` and builds `ChatAnthropic(model=id, api_key=key,
base_url="https://api.anthropic.com", timeout=120, max_retries=2, max_tokens=...)` once per
role (parameter names `base_url`, `timeout`, `max_retries` confirmed in the 1.7.5 source; the
explicit base URL is what stops the SDK reading `ANTHROPIC_BASE_URL`, confirmed in the 1.11.0
`_client.py`). `LiveModels` ignores the `scenario` argument of `graph_models` and
`baseline_model`, so no ground truth or `scenario_id` can reach a prompt through the factory. `cli.py` and `evals/commands.py` turn `--model` and `--dev` into
a factory in one shared function `model_factory(model, dev, environ)` in `models.py`, which exits
through the caller's `fail` with the R4 sentence when the key is missing.

`ask_model` (`src/agentops/nodes/__init__.py`, ADR 0002) builds the two-block system message,
calls the structured runnable with `include_raw=True`, validates, and returns the object plus an
`Action` filled by a new helper `usage_action(raw, latency_ms)`. The four node modules split
their prompt constants into a stable part and the fence sentence. `fakes.py` implements
`include_raw=True`, stamps usage and the model id, and keeps every queue rule as it is.
`baseline.py` gains `middleware: Sequence[AgentMiddleware] = ()` on `run_baseline` and
`usage_by_model` on `BaselineRun`. `evals/judge.py` uses `ask_model`. `evals/outcome.py` gains
`TokenUsage` and `RunOutcome.usage_by_model` (default empty, so every existing fixture still
validates). `evals/run.py` takes the factory, runs the judge when the factory says it may, and
renders the new columns. `pricing.py` is the one price module (R7, R9).

### Data

No database, table, policy or storage bucket. Two Pydantic models grow, both checkpointed and
both `extra="forbid"`: `Action` gains `cache_read_tokens: int = 0`, `cache_write_tokens: int = 0`,
`model: str | None = None`; `RunOutcome` gains `usage_by_model: dict[str, TokenUsage] = {}`.
Defaults keep every existing checkpoint and JSON fixture loadable (`CHECKPOINT_MODEL_TYPES`
unchanged). The audit log is not touched. What a live run sends to Anthropic: scenario text
(synthetic), the model's own earlier outputs, and whatever the operator typed at the approval
step: a reject reason (free text, length-bounded) and an edited remediation reach the next rca
prompt (`state.py` line 24, confirmed). No operator identity goes with it (`Approval.operator`
is a role literal, confirmed), but the README must say approval text is sent to the provider.
The key is never written to state, audit, trace or output.

### Interfaces

CLI: `--model scripted|anthropic` (unchanged), new `--dev` (flag, default off) on `run`,
`approve`, `resume`, `fork`, `eval`; new `--yes` on `eval`. Exit codes keep their meaning: 1
for the missing key and for the spend guard refusal, 2 for a run failure. Spend guard output:
`planned: <n> runs (<systems> × <scenarios> × <repetitions>) plus <m> judge calls on <ids>` then
`estimated cost: $<x.xx> (price table as of <date>; per-run estimate from design §11, upper
bound)`, or `estimated cost: unknown (unpriced: <id>)` when any role's id is not in the table,
then `Re-run with --yes to spend this.` Environment: `ANTHROPIC_API_KEY`,
`AGENTOPS_TEST_ANTHROPIC_API_KEY` (tests only), `AGENTOPS_MODEL_<ROLE>`. Eval table: four token
columns and `cost_usd mean/min/max` after `latency_ms`; a `judge cost: $<x> (<n> calls)` line
when the judge ran. `pricing.py`: `PRICES: dict[str, ModelPrice]`, `PRICES_AS_OF`,
`PRICES_SOURCE`, `cost_usd(usage_by_model) -> float | Unpriced`, `estimated_cost(model_ids:
Mapping[ModelRole, str], runs_per_system: Mapping[str, int], judge_calls: int) -> float |
Unpriced`, where `Unpriced` is a small frozen dataclass naming the first unpriced id. Live
workflow: inputs none; secrets `AGENTOPS_TEST_ANTHROPIC_API_KEY` only.

### Security and privacy

Threats and controls: (1) a key reaching a test, a trace or CI: `conftest.py` removes
`ANTHROPIC_API_KEY` for every test, the network guard blocks any non-loopback connect, `ci.yml`
is byte-identical and asserted to hold no `secrets.`; (2) money spent by accident: `--yes` plus
the profile's `ask_commands` entry for `--model anthropic`; the smoke test spends only when the
test-only variable is set; (3) a prompt injection steering a live model: unchanged defences
(fenced evidence, validated schemas, the registry) and unchanged adversarial tests, which now run
through the same `ask_model` path, and D12 adds a case that an injected log line appears only
between the fence markers in the new two-block format; (4) the approval interrupt: not touched;
the direct evidence is an empty `git diff main` over `nodes/approval.py`, `nodes/execute.py`,
`src/agentops/actions/` and `runbooks/` (the plan's verification). `agentops eval` resumes the
interrupt with `AUTO_APPROVAL` (`evals/run.py` line 53, confirmed, existing behaviour): with a
live model the code approves what the model proposed. This stays confined to `SimulatorSource`
runs under `eval`; any later evidence source must not inherit it, and `agentops run` still
pauses for a person; (5) new third-party import in a runtime path: exact pins, MIT licences
(confirmed from both wheels' metadata). `anthropic` 1.11.0 requires `httpx2<3,>=2.0.0`, `jiter`,
`anyio`, `docstring-parser`, `sniffio`, `typing-extensions` (confirmed from the wheel METADATA on
2026-10-05); `httpx2` is the real name, published from github.com/pydantic/httpx2 (confirmed on
PyPI), not a look-alike. The builder re-checks `pip show anthropic` after the install and records
the names in `docs/ci.md` under the existing transitive-pin debt; (6) secrets covered by
`.gitignore` as a pattern (`.env*`, confirmed) and checked by the existing `git ls-files` test;
(7) no new SaaS beyond the one the design already names, so the compliance regimes stay empty.
Anthropic receives synthetic scenario text plus operator approval text (Data section); the
profile's `retention_notes` names LangSmith retention but not Anthropic API retention
(believed: Anthropic keeps API inputs for a limited period under its own terms). The profile is
not edited by this change; adding Anthropic to `retention_notes` is an owner follow-up. The
test key comes from a separate workspace with its own spend limit (D11), recorded in
`docs/hosted-config.md`.

### Failure modes

| Dependency | Slow, down or wrong | Handling |
|---|---|---|
| Anthropic API down or 5xx | Client raises after the SDK's own retries | Node raises; `MODEL_NODE_RETRY` does not retry it (not a `ValidationError`), the run fails with exit 2 naming the type; escalate and postmortem fall back to code-written text as today; the eval row is `failed` with the type |
| API slow | Sync node blocks | `LiveModels` sets `timeout=120` seconds and `max_retries=2` explicitly (the SDK defaults are 10 minutes and 2, confirmed in the 1.11.0 `_constants.py`), so one hung call ends within about six minutes; the timeout error is an API error (row 1); recorded latency shows it; `TimeoutPolicy` does not apply (design §18) |
| Reply fails the schema | `parsed` is None | `model_validate_json` raises `ValidationError`, retried up to 3 attempts, every attempt an `Action` |
| Reply is valid but cites unknown evidence | `CitationError` | Retried, as today |
| Unsupported JSON-schema keyword | `anthropic.transform_schema` moves `minimum`, `maximum`, `minLength`, `pattern` into the description (confirmed in the 1.11.0 wheel); Pydantic still enforces them on parse | Covered by the retry path |
| `usage_metadata` or `response_metadata["model"]` absent on a reply | Tokens default to 0, model to the configured id (R6, E9) | Cost still attributable; a zero-token live call is visible in the table |
| Key missing | `MissingApiKeyError` | Exit 1 before any run, naming the variable |
| Price table lacks a model id | `cost_usd` and `estimated_cost` return `Unpriced` | Table prints `n/a (unpriced: <id>)`; the spend guard prints `unknown (unpriced: <id>)` and still needs `--yes`; never a wrong, partial or zero number |
| `ANTHROPIC_BASE_URL` set in the shell | Would redirect the key to another host | Ignored: the explicit `base_url` wins (R1, E1) |
| Smoke test skipped in the dispatch job | Zero passed tests | `check_test_count.py live.xml 1` fails the job |

### Observability

Every model call is an `Action` with latency, tokens, cache columns, model id and attempt; the
eval table and `--json` carry them; LangSmith tracing stays opt-in and unchanged. A failed live
run prints the exception type and where it was raised (`failure_summary`, unchanged).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Spend guard as a dollar threshold under which no `--yes` is needed | The first experiment is the only one planned; a threshold adds a path to spend by mistake for no saving |
| A `--judge` flag instead of running the judge with the live model | The judge would have no caller and the live path would ship untested; one call per graph row is in the estimate |
| Per-role CLI options instead of `AGENTOPS_MODEL_<ROLE>` | Six options on five commands; an environment override is enough for the rare experiment |
| Pin `anthropic` at the design's 1.9.0 | 1.11.0 is the latest on PyPI today (confirmed) and inside langchain-anthropic 1.7.5's `>=0.120.0,<2.0.0` range; a newer SDK is what the next Dependabot run would propose anyway |

## Decisions

ADRs: [0001](./adr/0001-models-module-is-the-only-importer-of-the-anthropic-client.md) one
importer, lazy; [0002](./adr/0002-read-usage-from-the-raw-reply-and-validate-in-the-node.md)
usage from the raw reply; [0003](./adr/0003-live-smoke-runs-from-a-second-workflow-file.md)
second workflow file; [0004](./adr/0004-cache-breakpoint-on-the-stable-system-block-and-the-baseline-prefix.md)
cache placement. The owner-facing decision rows are in `brief.md`.

## Open questions

None left open: each became a brief decision row with a recommendation. Two facts stay
`believed` until the owner's first live call: the exact model ids (constraints Q5 resolution,
2026-09-29) and the per-run token shape (design §11).

## Response to audit

The audit passed with no high finding; one revision, taken in full:

- Medium, spend guard with unpriced or mixed ids: R9 now prices role by role and prints
  `estimated cost: unknown (unpriced: <id>)` with `--yes` still required (D10, E38).
- Medium, home of the test key: R13 and R16 require a separate workspace with a monthly spend
  limit, recorded in a committed `docs/hosted-config.md`; the README says the app key must not
  be reused (D11, E30).
- Medium, injection in the two-block format: Task 1 gains E39 (D12).
- Low, `httpx2`: confirmed real from the wheel METADATA and PyPI; builder re-checks on build day.
- Low, guard lift: R12 needs the marker AND the test key (E34).
- Low, "synthetic text only": corrected in Data; README sentence added (R16); `retention_notes`
  is an owner follow-up, the profile is not edited here.
- Low, E9 against the configured-id fallback: both now say configured id (R6).
- Low, SDK exception in a node: E31 already raises one from a stub and asserts exit 2, the type
  in stderr and no key value; its row now says so.
- Low, timeout and retries: explicit in `LiveModels` (R1). Low, `ANTHROPIC_BASE_URL`: ignored by
  passing `base_url` explicitly (R1). Low, `AUTO_APPROVAL`: stated as confined (Security 4). Low,
  scenario argument: `LiveModels` ignores it (R1, E1). Low, boundary check: now an empty
  `git diff main` over the four paths (plan verification).

## Constraint audit

Filled by the constraint auditor. Severity: high blocks G2.

Audited 2026-10-05 against `.workhorse/profile.yml`, `docs/sdlc/constraints.md`, the repository
`CLAUDE.md`, the security baseline and the compliance skills. Regimes in the profile: none, so
no regime "At spec time" list applies; GDPR is listed as possibly applicable and was checked for
the data this change sends to a new recipient. Traceability: every brief Outcome clause maps to
at least one requirement (R1, R4, R6, R8, R9, R11, R12, R13, R14) and every plan task names its
requirements (confirmed by reading). Checked in the repository: `.gitignore` ignores `.env*` and
re-allows `.env.example` (confirmed, lines 4 and 5); `ci.yml` has no `secrets.` reference and no
`workflow_dispatch` (confirmed by grep); `approvals.md` holds no block yet (confirmed);
`state.py` line 24 says an operator's reject reason reaches the next rca prompt (confirmed);
`evals/run.py` line 53 resumes the interrupt with `AUTO_APPROVAL` (confirmed).

Audit result: pass

| Severity | Finding | Requirement affected | Resolution |
|----------|---------|----------------------|------------|
| medium | The spend guard says nothing about an unpriced model id or a mix of ids. `AGENTOPS_MODEL_<ROLE>` can point any role at an id missing from `PRICES` (for example a dearer model), and the plan's `estimated_run_cost(model_id, system) -> float` cannot return "unknown": it would either raise `KeyError` before the guard prints, or print a lower or zero dollar figure. The guard would then understate the cost exactly when the owner has changed the model. The estimate also takes one `model_id` per system, but a graph run uses four roles that may now have different ids. Rule: CLAUDE.md "never let a failure path be both silent and consequential"; brief guard "an unknown model id prints `n/a`, never a guess" is stated for the measured cost only, not for the estimate. | R9 (and R1 override), E16 to E18 | open (proposed D10) |
| medium | The test-only key has no stated home. Nothing in the spec or brief says `AGENTOPS_TEST_ANTHROPIC_API_KEY` must come from a separate Anthropic workspace with its own spend limit, so the owner may paste the app key into both the shell and the Actions secret. The workspace, its spend limit and the key's owner and rotation would then live only in the Anthropic console. Rules: security baseline "test credentials point at a dedicated test project, never production"; "config that lives only in a vendor dashboard is config that is not in version control" (committed hosted-config record). The no-fallback half of the rule is met by R13 and E26. | R13, R14, R16 | open (proposed D11) |
| medium | Task 1 changes how every node prompt is built (stable block plus fence block), and changes how the fake reads prompts (`prompt_lines` must read text from block lists). The plan only requires the existing injection tests to "pass unchanged". If the fake reads block lists wrongly, tests that assert injected text is absent outside the fence can pass on an empty string. Nothing in evals.md checks this with an adversarial payload in the new two-block format. Rules: repository CLAUDE.md "a change to either [the prompts or the registry] needs its adversarial tests updated in the same commit"; security baseline "deny-side assertions distinguish the failure modes". | R5, Task 1, E6, E7 | open (proposed D12) |
| low | `anthropic` 1.11.0 pulls transitive packages the spec lists as unpinned, one of them named `httpx2`. The spec marks this as confirmed from the wheel. The usual dependency is `httpx`, so the name should be checked against `pip show anthropic` after install, so that a transcription slip or a look-alike package is caught before it reaches `docs/ci.md`. The transitive-pin debt itself is existing and recorded. Rule: security baseline "new third-party import in a runtime path pinned to an exact version". | R3, Security and privacy item (5) | open |
| low | The network guard turns itself off for any test marked `live_smoke`, and only that test's own body checks for the test key. A later test that carries the marker without that check would run online, with the shell's environment, even under the keyless `ci.yml`. Note for the builder: lift the guard only when the marker and a non-empty `AGENTOPS_TEST_ANTHROPIC_API_KEY` are both present, or add a test asserting that `tests/test_live_smoke.py` alone uses the marker. The loopback allowlist also leaves out `::ffff:127.0.0.1` and other `127.0.0.0/8` addresses (believed harmless; it only fails closed). Rule: security baseline "CI must actually run the security tests" (the offline guard is the security test here). | R12, R17, E23 | open |
| low | Data section says "prompts carry synthetic scenario text only". This is not quite true. An operator's reject reason (free text, length-bounded) and an edited remediation reach the next rca prompt (`state.py` line 24, confirmed), so whatever a person types at the approval step is now sent to Anthropic. `Approval.operator` is a role `Literal` (confirmed), so no identity goes with it. Note for the designer: correct the sentence, and say in the README that approval text is sent to the model provider. The profile's `retention_notes` names LangSmith retention but not Anthropic API retention (believed: Anthropic keeps API inputs for a limited period under its own terms). Rules: GDPR possibly applicable (profile), "Art. 28 subprocessors: flag at the point of adding"; profile control "only synthetic data goes to Anthropic". | Data, Security and privacy item (7) | open |
| low | Two documents disagree on the model id when `usage_metadata` or `response_metadata["model"]` is absent. Failure modes and builder notes say it falls back to the configured id; E9 expects `model=None`. With `None`, `cost_usd` has no key to price. The builder should pick one; the configured-id fallback keeps cost attributable. Rule: testability (each acceptance check must be one test). | R6, E9 | open |
| low | The failure-modes table names an API error inside a node (exit 2, eval row `failed`), and the evals Failure target lists "API error", but no E case raises an SDK exception from a stub model. A case should also assert that stderr holds the exception type and no key value (failure taxonomy "Leaked"). Rule: testability (every failure mode has an eval category). | Failure modes row 1, R4 | open |
| low | The live client's timeout and retry count are left at SDK defaults (believed 10 minutes, 2 retries). A hung sync node can hold a live eval for half an hour. Note for the builder: set `timeout` and `max_retries` on `ChatAnthropic` explicitly in `LiveModels`. Rule: CLAUDE.md "never let a failure path be both silent and consequential". | R1, Failure modes row 2 | open |
| low | The SDK also reads endpoint variables such as `ANTHROPIC_BASE_URL` from the environment (believed, not verified in the 1.11.0 wheel). Such a variable would send the explicitly passed key to another host. R4 governs only where the key is read from. Note for the builder: pass the base URL explicitly or document the variable. Rule: security baseline "verify empirically before asserting". | R4 | open |
| low | `agentops eval --model anthropic` resumes the approval interrupt with `AUTO_APPROVAL` (`evals/run.py` line 53, confirmed), so remediations proposed by a live model are approved by code. This is existing behaviour, confined to `SimulatorSource`. The Security section should say so, so that a later evidence source does not inherit it. Rule: constraints "things that must not change" 1 (an approval record exists; no flag disables the interrupt). | Security and privacy item (4), R9, R11 | open |
| low | `ModelFactory.graph_models(scenario)` and `baseline_model(scenario)` hand the scenario to the live factory. `LiveModels` must ignore it, so that ground truth and `scenario_id` never reach the model. Note for the builder: one assertion in E1 that `LiveModels` reads no scenario field. Rule: constraints "things that must not change" 7. | R1, R10 | open |
| low | The plan's boundary check `grep -rn "dev\|ANTHROPIC\|yes" ...approval.py execute.py actions/` matches ordinary words and so proves little. `git diff main -- src/agentops/nodes/approval.py src/agentops/nodes/execute.py src/agentops/actions/ runbooks/` being empty is the direct evidence. Rule: security baseline "verify empirically before asserting". | Plan verification, Security and privacy item (4) | open |
| low | Three ask-first paths (`requirements.txt`, `pyproject.toml`, `.github/workflows/live-smoke.yml`) are approved only through brief D1 and D2, and `approvals.md` has no block yet. Builders hold on them until G2 is recorded. Rule: profile `sensitive_paths`. | R3, R13, R14 | open |
