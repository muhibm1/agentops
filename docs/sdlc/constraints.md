# AgentOps constraints

Written 2026-09-30 at onboarding by the discovery analyst. Sources: the approved design
`docs/superpowers/specs/2026-09-29-agentops-design.md` ("design §n"), its four companion specs,
the owner's brief to `/workhorse:onboard`, and what was observed on this machine. Every claim is
labelled confirmed or believed. The engineering record (Studbook) was not consulted: its tools
were not available in this session.

## How the business uses this

- **Who it is for.** A personal open-source portfolio project of Muhammad Muhibullah (GitHub
  `muhibm1`, Austin TX, US). There is no company, no customer and no end user. The audience is
  recruiters and hiring managers, specifically for a LangChain Deployed Engineer role (design §1,
  confirmed in the spec text).
- **What it must prove.** LangGraph state and control flow, interrupts and durable checkpoints,
  replay and forking, and an eval method with a measured result against a baseline (design §1).
- **The operational loop.** Five increments of at most two days each (design §16). Each one is a
  `/workhorse:run` change ending in green CI, a ship document and a demo. Week two grows the
  scenarios, adds a v2 graph, memory and the cookbook. Then come the workshop and the operator
  console (a separate repository).
- **Where a human decides today.** The owner approves every WorkHorse gate. He merges and pushes
  to `main` himself, and a merge to the public repository is a release. He alone spends money on
  live evals and LangSmith, and he publishes the LinkedIn posts, the workshop recording and the
  upstream LangGraph comment and pull request (cookbook and workshop specs, confirmed in the text).
  Inside the product, an operator approves, rejects or edits every remediation. That is the
  feature being demonstrated.

## Technical constraints

| # | Constraint | Status |
|---|---|---|
| T1 | Python 3.11. `langgraph dev` needs 3.11 or newer (design §13) | Python 3.11.5 from Anaconda, first on PATH: confirmed. Venv at `.venv`: believed, not created |
| T2 | pip with exact pins in `requirements.txt` (licence comment per line) and `requirements-dev.txt`. No uv (design §19 D10) | uv absent from PATH: confirmed. Files do not exist yet |
| T3 | Pins in design §13: langgraph 1.2.12, langgraph-checkpoint-sqlite 3.1.1, langgraph-checkpoint-postgres 3.1.2, langchain 1.4.3, langchain-core 1.6.6, langchain-anthropic 1.7.5, anthropic 1.9.0, langsmith 0.14.1, pydantic 2.13.5, pyyaml 6.0.3, typer (version open), langgraph-cli[inmem] 0.4.32 | The design says they were checked against PyPI on 2026-09-29. Not re-checked here: believed |
| T4 | CI never spends money and its default job reads no secrets. Unit tests use a scripted fake chat model. Live evals run only from `workflow_dispatch` with `AGENTOPS_TEST_*` secrets, and the job fails if any are missing (design §11) | Planned |
| T5 | Structured output via `with_structured_output(method="json_schema")`, because the design says Sonnet 5.5 rejects forced `tool_choice` (design §19 D6) | Believed (the spec's claim) |
| T6 | Nodes are sync in v1. `TimeoutPolicy` does not apply, so a hung source is bounded only by the adapter's own timeout (design §18) | Believed |
| T7 | The executor assumes at-least-once execution of its body. Exactly-once comes from the idempotency key and the ledger check (design §8) | Planned |
| T8 | Profile commands use the Windows path `.venv/Scripts/python.exe`. CI on Linux needs `.venv/bin/python` or the plain runner Python | Confirmed by reading the profile |
| T9 | The operator console is a separate TypeScript repository (`muhibm1/agentops-console`). This repository stays Python only (console spec §10 D1) | Confirmed in the spec text |

## Business constraints

- Public repository `muhibm1/agentops` under the MIT licence (owner's brief). The repository does
  not exist yet: `git remote -v` printed nothing (confirmed).
- No personal data, no users and no hosting in v1. The project runs as a local CLI and under
  `langgraph dev` (owner's brief, design §3 and §17).
- Total model spend is budgeted at $150 to $250. The first experiment is under $10 (design §11).
  These are the spec's own estimates: believed.
- Nothing proprietary from the owner's employer may appear, including in the data-pipeline
  scenarios 4 and 5 (design §6, workshop spec §5).
- Cut and not planned: a web UI in this repository, cloud infrastructure, a second model provider,
  real Kubernetes or cloud targets, and automatic execution under any configuration (design §3).

## Things that must not change

1. Nothing executes without an approval record in state. No flag, environment variable or
   config may disable the interrupt (design §17).
2. The allowed-action set is a typed runbook loaded into a registry. The prompt does not define
   it. An action outside the registry routes to `escalate` (design §7).
3. Control fields (`status`, `investigation_budget`, `loops`, `approval`, `execution`) are never
   part of a model output schema (design §5).
4. Escalation is a terminal success state on ambiguous scenarios (design §19 D9).
5. The held-out split is scored only when a version is frozen, and never to steer a change. Its
   bytes are pinned by a committed checksum (design §11, §19 D5).
6. The audit log is append-only (design §17).
7. Evidence payloads are untrusted data (design §17). Scenario ground truth and `scenario_id`
   never reach the model (design §5).
8. The README publishes the failures and the spread, not only the means (design §11).

## Known debt and gaps at onboarding

- `.gitignore` (confirmed contents) ignores only `.worktrees/`, `.workhorse/active` and
  `.workhorse/state/`. It does not cover `.env*`, `.venv/`, `__pycache__/`, the SQLite checkpoint
  file or `audit/`. Fix this in increment 1, before the first `.env` exists, and confirm with
  `git ls-files`.
- `.gitignore` and `.workhorse/profile.yml` are untracked (confirmed by `git status`).
- No lockfile with hashes. Exact pins only. The design defers a lockfile (§19 D10).
- The build check is believed: `langgraph build` may need Docker, so the profile's `build` is
  empty (decision D3).
- The profile's `format` command runs from the editor hook on every write. Until `.venv` exists
  it fails with a non-blocking message (confirmed on the write of `profile.yml`).

## Compliance signals (for wh-compliance-mapper)

- Industry: personal / careers, an individual with no company. Region: US (Austin TX).
  Repository: public, MIT.
- Data categories: none. Scenarios, logs and evidence are synthetic (design §17). The only
  person-identifying field is `Approval.operator` (open question Q4).
- Third-party processors once code exists: Anthropic (model API) and LangSmith (traces).
  Neither receives personal data, provided Q4 is answered with a role string.
- Later scope: `EngineSource` reads the owner's own my trading engine trading-engine event store (design §15). Its
  contents may include financial records of the owner's own system. This is open in design §20
  Q1 and is not in v1.
- No policy documents exist in the repository. `compliance.regimes` is left empty: the repository
  states none.

## Mistakes to avoid

Carried from the owner's other repositories and from this onboarding. Newest first.

- `deny_commands` entries match as case-insensitive substrings of the entire shell command. A
  table-drop phrase in that list blocked an ordinary edit during onboarding. Scenario 8's
  fixture contains that phrase on purpose, so it is not on the list. Do not add it back.
- The WorkHorse bash guard (`hooks/scripts/bash-guard.js`, 0.3.13) denies any `git push` that
  names `main`, and any force push, before the profile lists are read. Only the owner pushes
  `main`. Feature-branch pushes are allowed.
- The agent harness refuses to create or edit `.env`, `.env.*` (including `.env.example`) and
  `.npmrc`. The owner creates those files by hand. `.env*` is also a protected path in the
  profile.
- Never score the held-out split to steer a change.

## Open questions for the owner

| # | Question | Why it matters |
|---|---|---|
| Q1 | Where does the held-out split live: `scenarios/held-out/`, a list in `evals/`, or something else? | The profile's four held-out globs are guesses. A wrong glob fails to protect the split |
| Q2 | Is the repository public from the first push, or private until increment 5 ("The repository is public", design §16)? | Decides when GitHub gates (D1) and branch protection can be switched on |
| Q3 | Will `main` have branch protection that requires CI, even as a sole committer? | This is your global standard. It cannot be set until the remote exists |
| Q4 | What does `Approval.operator` hold: a fixed role string such as `operator`, or an OS username? | A username is a personal identifier in checkpoints, audit logs and LangSmith traces |
| Q5 | Which `typer` version, and which exact model id for "Sonnet 5.5"? | Both need an exact pin (design §13 leaves typer open) |
| Q6 | Design §20: the trading-engine adapter, LangSmith Plus versus `langgraph dev`, and who publishes the LinkedIn post and workshop | Carried over from the design, still open. None blocks v1 |
| Q7 | Should running a live eval locally ask first? | Live evals spend money and could score the held-out split. The CLI flags are not designed yet, so no `ask_commands` entry can name them (D7) |

---

### Resolutions (2026-09-30, main session)

| Q | Resolution | Source |
|---|---|---|
| Q1 | `evals/held_out/`, pinned by `evals/checksums.sha256`; the globs stay as written and cover it | Recommendation; owner may override at the first G2 |
| Q2 | Private now (`muhibm1/agentops`, created 2026-09-30), public at increment 5. GitHub gates on: `github.approvals: true` | Owner, 2026-09-30 |
| Q3 | Branch protection on `main` requiring CI once the repository is public (GitHub requires a paid plan for protection on a private repository, believed) | Recommendation |
| Q4 | A role string only, enforced in the schema by a `Literal` or pattern, never a name or username | Recommendation, matches the compliance mapper's control 1 |
| Q5 | `typer` pinned at scaffold from PyPI on the day; model id `claude-sonnet-5-5`, development runs on `claude-haiku-4-5-20251001` | Recommendation; the model ids are confirmed on the Anthropic models page 2026-09-29 |
| Q6 | Open; none blocks increments 1 to 3. API spend is decided by the owner before increment 4 | Owner, 2026-09-30 |
| Q7 | Yes: live evals ask first. An `ask_commands` entry is added in the change that introduces the CLI flag | Recommendation |

## G0: confirm the profile

**The short version.** AgentOps is greenfield. I wrote the WorkHorse profile, the codebase map
and `CLAUDE.md` from the approved design. Every command and stack entry is believed until
increment 1 builds. Confirm the protected paths, the autonomy level, the commands and the empty
compliance regimes below, or edit `.workhorse/profile.yml` first.

**What you are confirming** (`.workhorse/profile.yml`)

| Area | Value | Status |
|---|---|---|
| Autonomy | `bounded` | Template default |
| Protected (deny) | `.env*`, `**/*.pem`, `**/*.key` | As you specified |
| Sensitive (ask) | `.github/workflows/**`, `requirements*.txt`, `pyproject.toml`, `langgraph.json`, `runbooks/**`, `evals/checksums*`, the held-out globs | As you specified. Held-out globs believed (Q1) |
| Sensitive, added by me | `.github/dependabot.yml`, `.githooks/**`, `.gitignore`, `.workhorse/profile.yml`, `LICENSE` | D6 |
| Commands | install, typecheck (pyright), lint (ruff check), format (ruff format), test and test_file (pytest), security_audit (pip_audit), all through `.venv/Scripts/python.exe` | Believed: none can run until the venv and requirements exist |
| Commands left empty | build (D3), e2e (live evals cost money), screenshot (no UI) | Deliberate |
| Deny commands | `git push --force`, `git push -f`, `npm publish`, `twine upload`, `DROP DATABASE` | As you specified, plus two publish guards |
| Ask commands | pushes naming `main`, `gh repo create`, `gh repo edit`, `gh release create`, `gh workflow run`, `langgraph deploy` | Bare `git push` is not listed, so feature-branch pushes are not blocked |
| Compliance | regimes `[]`, special categories `[]`, residency `none` | For wh-compliance-mapper to confirm |
| Branches | default `main`, prefix `wh/`, conventional commits | As you specified |
| Environments | dev, staging and prod with no deploy command | Nothing hosted in v1 |

**Proof**

| Check | Command | Exit | Status |
|---|---|---|---|
| Profile parses | `node .../workhorse/0.3.13/scripts/wh.js status` | 0 ("No active change.") | confirmed |
| Parsed values match the table | the plugin's `loadProfile`, dumped to JSON | 0 | confirmed |
| Python 3.11 present | `python --version` reports 3.11.5 | 0 | confirmed |
| `gh` signed in as `muhibm1` | `gh auth status` | 0 | confirmed |
| Lint, typecheck, test, audit | not run: no code or venv | n/a | not run |

**Decisions**

| # | Decision | Recommendation | Alternative | Why |
|---|---|---|---|---|
| D1 | GitHub gate approvals | `github.approvals: false`, approvers `[muhibm1]`. Turn it on once `muhibm1/agentops` exists | `true` now | There is no remote, so a gate push would fail. Needs Q2 answered |
| D2 | Held-out path globs | `**/held-out/**`, `**/held_out/**`, `**/*held-out*`, `**/*held_out*` | Wait for the layout | Over-protecting costs one prompt. Under-protecting breaks eval integrity |
| D3 | `build` command | Empty until increment 1 confirms that `langgraph build` validates without Docker, or adds a JSON-schema check | Set it now | A believed command that fails would put a false red in every verification |
| D4 | Tier floors | Tier 3: `src/agentops/actions/**`, `nodes/approval.py`, `nodes/execute.py`, `runbooks/**`. Tier 2: `evals/**`, `state.py`, `graph.py`, `.github/**` | Classifier only | These files are the security boundary and the eval integrity |
| D5 | `build.max_parallel` | 4 | 10 (template) | One person and one Windows machine. Reversible |
| D6 | Extra sensitive paths | Add the five listed above | Your list only | Each one changes CI, hooks, the licence or the profile itself |
| D7 | Live-eval guard | No `ask_commands` entry yet. Add one when the eval CLI's live flag exists (Q7) | Guess a flag now | A guessed substring protects nothing |

**Your decision.** G0 has no approval record. Committing `.workhorse/`, `docs/sdlc/` and
`CLAUDE.md` confirms the profile and accepts every recommendation above, unless you edit the
profile before committing. To change something, edit `.workhorse/profile.yml` and rerun
`workhorse status`.
