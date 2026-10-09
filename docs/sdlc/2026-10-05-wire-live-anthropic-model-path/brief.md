# Wire the live Anthropic model path

Change id: `2026-10-05-wire-live-anthropic-model-path` · Prepared 2026-10-05 17:45 UTC
Risk tier: 2 (new third-party integration on a paid API, with edits to the ask-first paths `requirements.txt`, `pyproject.toml` and `.github/workflows/**`; no money moves in this change because nothing in its build, tests or CI default job calls a model, and the action layer, approval node and runbook are untouched)
For the agents: [spec.md](./spec.md), [plan.md](./plan.md), [evals.md](./evals.md), [adr/](./adr/)

## The short version

You asked for `--model anthropic` to stop being a stub so the first paid experiment can happen,
safely and measurably. This change builds the live model path in one new module, records the
tokens and model id of every call so the eval table prints a measured cost per run, and puts a
spend guard in front of any live eval. The one thing worth knowing: no agent and no test makes a
live call in this change; every test runs behind a guard that blocks network connections, CI's
default workflow file is not edited at all, and the first real call is yours, behind `--yes`.

## Problem

Today `agentops run --model anthropic` and `agentops eval --model anthropic` exit 1 with "not
wired yet". The graph, the naive baseline it is measured against, and the model-graded judge can
only run on the scripted model, which replays answers written into the scenario files. That
means the headline result the project exists to show, graph against baseline on a real model,
cannot be measured, and nobody knows what a run costs.

## Outcome

When this is done: `agentops run --scenario clean_bad_deploy --model anthropic` investigates on
Claude Sonnet 5.5 (or Haiku 4.5 with `--dev`), reading the key from `ANTHROPIC_API_KEY` in
your shell and nothing else, and exiting with one clear sentence if it is missing. Every model
call's tokens (input, output, cache read, cache write) and model id are saved in the run's
state. `agentops eval --model anthropic` first prints how many runs it will make and what they
should cost, from a price table with its date, and refuses to continue without `--yes`. The
table then shows measured tokens and dollars per run for the graph and the baseline, plus the
judge's score and its own cost. The test suite still needs no key and opens no network
connection, proven by a guard that fails any test that tries. One opt-in smoke test exists for
you to run with a test-only key, and a manually triggered CI job runs it, failing if the key is
missing or the test was skipped.

## What changes for people

You, as operator: new `--dev` flag on the thread commands and `eval`, new `--yes` on `eval`,
three variable names documented in the README (`ANTHROPIC_API_KEY`, the test-only
`AGENTOPS_TEST_ANTHROPIC_API_KEY`, and `AGENTOPS_MODEL_<ROLE>` to try another model id for one
role). No `.env.example` is written by agents; the names are in the README instead. Anyone
reading the repository: a `models.py` and a `pricing.py`, two new pinned dependencies, a second
workflow file, a `docs/hosted-config.md` naming the test workspace, and a README section that
says what the live model costs, how the suite stays offline, and that approval text is sent to
the model on a live run. Nothing changes for the scripted demo, the approval step, the executor
or the runbook.

## What could go wrong, and the guard for each

| Risk | Guard |
|------|-------|
| A test, a builder or CI spends money by accident | No live call anywhere in the change; tests delete `ANTHROPIC_API_KEY` and block non-loopback connections; `ci.yml` is byte-identical; the live job runs only when you trigger it; the profile already asks before `--model anthropic` |
| A key leaks into output, state, audit or a trace | The key is passed to the client and never printed; the missing-key message names the variable only; tests assert stdout is empty on that error |
| The cost printed is wrong | Cost comes from the reply's own usage numbers and a price table with a date and source; an unknown model id prints `n/a`, never a guess |
| The spend guard underestimates | The estimate uses the design's upper-bound token figures per role, and the planned run count is exact; an unpriced id prints `unknown`, never a partial figure (D10); the measured table replaces the estimate after your first run |
| A live call hangs or goes to the wrong host | Each call has an explicit two-minute timeout and two retries; the API address is fixed in code, so a stray `ANTHROPIC_BASE_URL` in your shell is ignored |
| What you type at the approval step reaches Anthropic | True on a live run: a reject reason or an edited remediation is sent to the model, with no name attached. The README says so; keep personal data out of that box |
| The live job in CI passes with zero tests | It fails if the test-only secret is blank, and fails if the smoke test was skipped (the existing test-count script with a floor of 1) |
| A model id is wrong on the day | The ids come from the design's resolution of 2026-09-29 (believed, not re-checked today); `AGENTOPS_MODEL_<ROLE>` changes one without a code change, and the smoke test on Haiku is the cheap first probe |
| The structured-output API rejects our schemas | The SDK rewrites unsupported constraints into descriptions (confirmed in the pinned wheel); Pydantic still enforces them, and a bad reply is retried as today |
| Prompt injection steers a real model | Same fences, validated schemas and allowlist as today; the adversarial tests run through the same code path |

## Decisions

Every question the design raised, each with the recommended answer already chosen. Approving
this document accepts every recommendation; say otherwise in the approval notes to change one.

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D1 | Pins in `requirements.txt` (ask-first path) | `langchain-anthropic==1.7.5` and `anthropic==1.11.0`, both MIT, both the latest on PyPI today (confirmed); the builder re-confirms on build day and the range check against `langchain-core==1.6.6` passes (confirmed from the wheel) | The design's `anthropic==1.9.0` | Newer SDK inside the library's declared range; Dependabot would propose it next week anyway. Also approves the `pyproject.toml` edit that registers the `live_smoke` test marker |
| D2 | Where the live CI job lives (ask-first path) | A second file, `.github/workflows/live-smoke.yml`, manual trigger only, reading only `AGENTOPS_TEST_*` secrets | A second job inside `ci.yml` | `ci.yml` stays untouched, so "the default job reads no secret" stays a fact the existing test proves byte for byte |
| D3 | Spend guard | Every live eval prints the run count and estimate and needs `--yes`, whatever the size; `--held-out` is still required for the frozen split | A dollar threshold under which no `--yes` is needed | One planned experiment; a threshold adds a way to spend by mistake and saves nothing |
| D4 | Prompt caching | Cache breakpoint on the stable part of each node's instructions, and the library's caching middleware on the baseline's growing conversation | Skip caching in the graph nodes | The baseline saving is real. Honest note: today's node instructions are shorter than the cache minimum (believed 1024 tokens on Sonnet, 4096 on Haiku), so the node breakpoint will record no cache writes until the prompts grow; the new cache columns show you whether it did anything |
| D5 | Prices | The builder transcribes the four prices per model from the Anthropic pricing page on build day with the date and URL in the module; you check the numbers at Ship | I write them now from memory | I cannot read the page in this session and will not invent a price; an unpriced id prints `n/a` rather than a wrong number |
| D6 | Default model ids | `claude-sonnet-5-5` by default, `claude-haiku-4-5-20251001` with `--dev`, from the constraints Q5 resolution (believed) | Re-check the models page before approving | A wrong id fails loudly on the first call and `AGENTOPS_MODEL_<ROLE>` fixes it without a code change |
| D7 | The judge | Runs inside `agentops eval --model anthropic` on every graph row (one extra call each, counted in the estimate), its cost printed on its own line | A separate `--judge` flag | Otherwise the judge has no caller and the live path ships untested; the judge's cost stays separate from the systems' cost |
| D8 | `.env.example` | Not created; the README documents the variable names. If you want the file, create it by hand with the three names and no values; `.gitignore` already allows it | Agents create it | The harness refuses to write `.env*` files (confirmed in two earlier changes) |
| D9 | The CI test-count floor | Left at 700; `ci.yml` is not edited | Raise it with the new tests | Keeping `ci.yml` byte-identical is the clearest keyless-CI evidence; raising the floor is a one-line note for the next change |
| D10 | Spend guard when a model id has no price, or roles use different ids (from the audit) | The estimate is computed role by role with each role's own id; if any id is not in the price table the guard prints `estimated cost: unknown (unpriced: <id>)` and still requires `--yes`; it never prints a partial or zero figure. A new test case (E38) proves it | Price the roles it can and print that figure | A lower figure printed exactly when you have swapped in a dearer model is the one way the guard could mislead you; "unknown" is honest and still stops the run |
| D11 | Where the test-only key comes from (from the audit) | `AGENTOPS_TEST_ANTHROPIC_API_KEY` is a key from a separate Anthropic workspace with a low monthly spend limit, never the app key. A committed `docs/hosted-config.md` records the workspace name, the limit, the key's owner and its rotation (names only, you fill the values in); the README says the app key must not be reused. Follow-up for you, not done by this change: add Anthropic's API retention to the profile's `retention_notes` (the profile is not edited here) | Reuse the app key for the smoke test | A test key with its own spend cap bounds what a mistaken job can cost, and the record keeps the console settings visible in the repository |
| D12 | An extra adversarial test in task 1 (from the audit) | With the system prompt now split in two blocks, one test (E39) runs the scenario with the planted log-line injection and asserts the injected text appears only between the evidence fences, and that the system text it checks is not empty | Rely on the existing injection tests passing | The existing tests could pass against an empty string if the fake read the new block format wrongly; this one cannot |

## How it will be proved

Profile checks: `pyright`, `ruff check`, `ruff format --check`, `pytest -q`, `pip_audit`, and
the install command after the pin change. Eval cases in `evals.md`: 37 cases (18 golden, 8 edge,
4 failure, 7 adversarial, with 3 non-functional rows), all but one run inside `pytest` on the
scripted model or stubs. The one exception, E27, is the live smoke test: it is skipped unless the
test-only variable is set, and is listed as "not verified" until you run it. The verifier also
quotes three facts: `ci.yml` has no diff against `main`, no `.env*` file is tracked, and the
approval, execute, action and runbook files have no diff against `main`. Done means: a scripted
`agentops eval` prints the new cost columns as `n/a (unpriced: scripted)`, a live eval without
`--yes` prints the plan and stops (with a figure, or `unknown` for an unpriced id), and the suite
is green with the network guard on.

## Estimate

Waves: 4. Tasks: 9. Agent-time budget at tier 2: 90 minutes.

## Your decision

Approve to build it exactly this way, or reject with notes to have it redesigned. Approving also
accepts the three ask-first edits (D1, D2) so the builders are not held on them. The constraint
audit passed; its three medium findings became D10 to D12 and are taken. After the merge the
first live call is yours: create the test workspace with its spend limit (D11), fill in
`docs/hosted-config.md`, set `AGENTOPS_TEST_ANTHROPIC_API_KEY` and run the smoke test, then
`agentops eval --model anthropic --dev` and answer `--yes` to the plan it prints.
