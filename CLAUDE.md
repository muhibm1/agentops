# AgentOps: working in this repository

A LangGraph incident-response runtime: it investigates a simulated production failure, proposes a
remediation with cited evidence, stops for a human approval, and executes only allowlisted actions
idempotently. Python 3.11, MIT. The approved design is
`docs/superpowers/specs/2026-09-29-agentops-design.md`; read it before your first change.

## Commands

- Setup: `python -m venv .venv`, activate it (`.venv\Scripts\activate` on Windows,
  `source .venv/bin/activate` elsewhere), then
  `python -m pip install -r requirements.txt -r requirements-dev.txt`
- Lint: `python -m ruff check .`; format check: `python -m ruff format --check .` (fix with
  `python -m ruff format .`)
- Typecheck: `python -m pyright`
- Test: `python -m pytest -q` (no API key needed; tests use a fake chat model)
- Dependency audit: `python -m pip_audit -r requirements.txt`
- Demo: `agentops run --scenario clean_bad_deploy --model scripted` (no API key; prints the
  hypotheses, loops, remaining budget, evidence count, then root cause and remediation or the
  escalation reason and summary). `--model anthropic` exits 1: the live model is not wired yet.
  `langgraph dev` needs `langgraph.json`, not yet in the repository.

## Architecture in five lines

1. `src/agentops/graph.py` builds a `StateGraph` over one typed `IncidentState` (`state.py`):
   triage, a fan-out router, three parallel evidence nodes, merge (owns the investigation budget
   and the loop count), rca, escalate; approval, execute, verify and postmortem arrive in
   increment 3. A run ends at `awaiting_approval` or `escalated`.
2. The model runs only in triage, rca, postmortem and escalate. Every edge, budget, retry and side
   effect is code. Model outputs are validated Pydantic models that never contain control fields.
3. Evidence comes through the `EvidenceSource` protocol (`sources/`); v1 uses `SimulatorSource`
   over `scenarios/*.json`. Every evidence payload is untrusted data.
4. `approval` calls `interrupt()`. `execute` goes through `actions/`: registry from
   `runbooks/default.yaml`, preconditions, idempotency key, ledger check, dry run, append-only
   audit log.
5. `evals/` compares a ReAct baseline (`baseline.py`) against the graph on dev and held-out splits
   in LangSmith. CI runs no live model and reads no secrets.

## Conventions

- Branches: `<prefix>/<short-name>` (WorkHorse uses `wh/<change-id>`); commits: Conventional
  Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`)
- Tests live in `tests/` as `test_*.py`; never edit a test to make it pass
- Every dependency is an exact pin, with its licence as a comment in `requirements.txt`
- No flag, env var or config may disable the approval interrupt
- New actions go in the runbook, never in a prompt
- Never score the held-out split to steer a change; score it only when a version is frozen

## Protected

Never edit: `.env*`, `**/*.pem`, `**/*.key`.

Ask first: `.github/workflows/**`, `.github/dependabot.yml`, `.githooks/**`, `requirements*.txt`,
`pyproject.toml`, `langgraph.json`, `runbooks/**` (the action allowlist is a security
boundary), `evals/checksums*` and the held-out split (eval integrity), `.gitignore`, `LICENSE`.

Only the owner pushes to `main`.

## Mistakes to avoid

Appended by retro after each change. Newest first.

- Merging the pull request to `main` is the release, and the owner does it. Stop at the Ship gate;
  never dispatch the shipper in deploy mode to merge (the permission classifier denies it every
  time). Push gate and retro records to the change branch before the merge, not after.

- Never put a table-drop phrase in the profile's `deny_commands`: entries match substrings of
  the whole shell command, and scenario 8's fixture contains that phrase on purpose.
- The agent harness refuses to create `.env`, `.env.example` or `.npmrc`; the owner creates
  them by hand.
- On this machine the `workhorse` npm shim fails under Git Bash; run
  `node ~/AppData/Roaming/npm/node_modules/workhorse-plugin/scripts/workhorse.js`.
- Never score the held-out split to steer a change.

## Workflow

Optional: this repository is built with WorkHorse. With it installed, start a change with
`/workhorse:run "<problem> -> <outcome>"`; artifacts live in `docs/sdlc/<change-id>/` and
approvals are recorded with `/workhorse:approve`. Read `.workhorse/profile.yml`,
`docs/sdlc/codebase-map.md` and `docs/sdlc/constraints.md` first. Without WorkHorse, a branch, the
commands above and a pull request are all you need.
