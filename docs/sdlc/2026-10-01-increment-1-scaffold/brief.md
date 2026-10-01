# Increment 1 scaffold

Change id: `2026-10-01-increment-1-scaffold` · Prepared 2026-10-01 03:20 UTC
Risk tier: 2 (edits the CI workflow, Dependabot, `state.py` and `graph.py`, all tier-2 floor paths in the profile; nothing moves money or data, so not tier 3)
For the agents: [spec.md](./spec.md), [plan.md](./plan.md), [evals.md](./evals.md), [adr/](./adr/)

## The short version

You asked for the first increment of the approved design: a Python package with the state
model, the evidence interface and simulator, two scenarios, a fake model for tests, a
three-step graph, a command line that reaches root-cause analysis without an API key, and CI
that proves it. That is what will be built, in seven tasks over four waves. The one thing worth
knowing: the fake model and its scripted answers ship inside the package and the scenario
files, so the demo command and the tests run the same code path. This change also edits eight files
on seven "ask first" patterns, including the WorkHorse profile; your approval here is the ask
(D1, D16). The auditor passed the design with one medium and eight low findings; D16 and D17
and the builder notes in the plan answer them.

## Problem

In your words: AgentOps has an approved design but no code, no environment and no CI. Stated
precisely: the repository holds five design documents and a WorkHorse profile whose every
command is marked "believed" because nothing exists to run. Nobody can install, test or
demonstrate anything.

## Outcome

When this is done: `pip install` from the two requirements files works on a clean checkout;
`agentops run --scenario clean_bad_deploy --model scripted` prints hypotheses, the collected
evidence count, the root cause `bad_deploy` and the remediation `rollback_deploy` in under five
seconds; the same works for `deploy_red_herring_db`; 40 eval cases pass locally with no API key
and no network; and the GitHub Actions run for the branch is green with lint, type check,
tests above a floor of 30, and a dependency audit. Every profile command changes from
"believed" to confirmed.

## What changes for people

You (the owner) get a repository you can clone, install and demo. A contributor gets
`CONTRIBUTING.md`, a pre-push hook and CI that rejects an unpinned dependency, a secret in the
workflow, or a shrinking test suite. No user, operator or end customer exists yet. The agents in
later increments inherit a state model whose output schemas provably exclude the control
fields, and a scenario format that hides ground truth from the model by construction.

## What could go wrong, and the guard for each

| Risk | Guard |
|------|-------|
| A pinned version disappears or a newer one is needed on build day | The builder re-checks every pin on PyPI before committing; the versions in the plan were confirmed on 2026-09-30 |
| The graph silently fails to retry a malformed model answer | The retry classes are named explicitly, and a test feeds a bad answer then a good one and asserts the good one wins (ADR 0002) |
| Scenario answers or ground truth leak into the model's prompt | A test records every prompt and asserts none of the hidden sections appear; the simulator has no accessor for them |
| CI goes green while running almost no tests | A floor of 30 passed tests fails the job; a unit test proves the floor script itself |
| An action in CI is pinned to a moving tag or references a secret | A test parses the workflow and fails on any `uses` without a 40-character commit SHA or any `secrets.` reference; Dependabot updates the SHAs |
| The dependency audit finds a problem with no fix | The advisory id and reason go into `docs/ci.md` and only that id is ignored, never the audit |
| The harness refuses to write a protected filename | None is in the edit list; `.env.example` waits for increment 2 when the first key is needed (D8) |
| A log line contains the marker that ends the "untrusted data" block, so the model reads what follows as instructions | The evidence step breaks the marker characters in every payload before it reaches the model; a test feeds a log line with the marker and an instruction and asserts the block stays closed (D17) |

## Decisions

Every question the design raised, each with the recommended answer already chosen. Approving
this document accepts every recommendation; say otherwise in the approval notes to change one.

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D1 | Seven "ask first" patterns are touched, eight files: `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.githooks/pre-push`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `LICENSE` (all created) and `.workhorse/profile.yml` (edited, D16) | Authorised by this request; this approval is the ask | You create or edit them by hand | The request names each one; the plan marks them in task headings |
| D2 | Where the fake model lives | `src/agentops/fakes.py`, inside the package (design said `tests/fakes.py`) | Keep it under `tests/` | The demo command must import it after a plain install (ADR 0001) |
| D3 | Where the fake's answers live | A `scripted_outputs` section in each scenario file, never served by the simulator | A fixtures folder under `tests/` | One file per scenario; same hiding rule as ground truth (ADR 0001) |
| D4 | Which packages to pin now | Only the four this increment imports (langgraph, langchain-core, pydantic, typer) plus dev tools | Pin the whole design stack now | Unused pins create audit and Dependabot noise; each arrives with the code that needs it |
| D5 | `--model` option on `agentops run` | Required, values `scripted` or `anthropic`; `anthropic` exits with "arrives in increment 2" | Default to `scripted` | A default that silently becomes a paid model later is a trap |
| D6 | How CI installs | Creates `.venv` and runs every tool through it, matching the profile commands | Use the runner's Python directly | One install shape for humans and CI (ADR 0003) |
| D7 | `langgraph.json` and the build check | Deferred to increment 3 with the checkpointer; profile `build` stays empty | Add a stub now | A stub that cannot be validated adds a sensitive file with no test |
| D8 | `.env.example` | Deferred to increment 2; you create it by hand then (the harness refuses the filename) | Create now | No environment variable is read in this increment |
| D9 | Test-count floor | 30 | 1 (just non-zero) | Above any accidental subset, below the 38 planned test cases |
| D10 | `Approval.operator` | A literal of three roles: `operator`, `on-call`, `reviewer` | A text pattern | A pattern cannot reject a username; a literal can (profile control 1) |
| D11 | Incident clock | Add `opened_at` to the state so evidence windows are deterministic | Let the simulator own the clock | The state is the whole truth of a run (design §5); a hidden clock breaks replay later |
| D12 | Installing the package itself | `-e .` as the last line of `requirements-dev.txt` | Change the profile's install command | The profile command stays as written; no sensitive profile edit |
| D13 | Retry classes on model nodes | Name `CitationError` and `ValidationError` explicitly | LangGraph's default list | The default is believed not to retry validation errors; a test settles it (ADR 0002) |
| D14 | Action pins | `actions/checkout` v7.0.1 and `actions/setup-python` v7.0.0 by commit SHA, confirmed today; Dependabot moves them | Tag pins | A moved tag changes what runs |
| D15 | What `required_evidence` holds | Evidence ids declared in the scenario | Kind names as in the design example | An id is checkable with no mapping table; the increment-4 evaluator checks membership directly |
| D16 | The WorkHorse profile (auditor's medium): it still says every command is "believed", points the fake model at `tests/fakes.py`, and its `format` command rewrites files | Do it: the last task edits the profile; each command loses "believed" only after it has run; the fake-model path becomes `src/agentops/fakes.py`; `format` becomes a check-only command, with the rewriting form documented for local use | Leave the profile stale for a later change | The outcome promises confirmed commands, and a check that rewrites files while verifying is a hazard; a test now reads the profile and fails if it drifts |
| D17 | A log line could contain the marker that closes the "untrusted data" block in the prompt (auditor's low) | Fix it now: the evidence step breaks the marker characters in every payload before it enters the run's state; the simulator keeps the original text; the existing prompt test gains a hostile log line | Reject the item, or wait for the injection scenario in increment 2 | One small boundary function now is cheaper than an incident later; rejecting would let a hostile line blind the investigation instead of merely failing to hijack it |

## How it will be proved

Profile checks that must pass: lint, format check, typecheck, test, security audit, all exit 0.
Eval cases in `evals.md`: 40 in total, all machine-run: 18 golden, 5 edge, 8 failure, 7
adversarial, 2 non-functional (suite under 60 s, demo command under 5 s). One requirement, the
green GitHub Actions run, has no local case; you read the run on the pull request. Done means:
a clean checkout installs, both scenarios reach root cause from the command line, every case
passes, and CI is green.

## Estimate

Waves: 4. Tasks: 7 (one, then four in parallel, then one, then one). Agent-time budget at tier
2: 90 minutes.

## Your decision

Approve to build it exactly this way, or reject with notes to have it redesigned. Approving
also authorises the "ask first" paths in D1, including the profile edit in D16, and accepts D2
to D17 as written.
