# Continuous integration

## What CI runs

`.github/workflows/ci.yml` runs one job on every push and pull request, with a read-only token
(`permissions: contents: read`) and no secrets. It creates a venv and runs, in order:

1. Install: `python -m pip install -r requirements.txt -r requirements-dev.txt`
2. Lint: `ruff check .`
3. Format check: `ruff format --check .`
4. Typecheck: `pyright`
5. Tests: `pytest` with a JUnit XML report
6. Test-count floor: `scripts/check_test_count.py` reads that report
7. Dependency audit: `pip-audit -r requirements.txt`

No step calls a live model or reads an API key, and no step sets a LangSmith tracing variable.
The install step brings in `langgraph-cli[inmem]` with the development requirements, but no step
and no test runs it (it sends usage analytics when it runs; see the README). No step runs
`agentops eval`, and no test scores the committed held-out split. Every action is pinned to a full commit SHA, with
its tag in a comment.

## The live smoke job

`.github/workflows/live-smoke.yml` is a second workflow that runs only when started by hand
(`workflow_dispatch`), never on a push or a pull request. It has the same read-only token and
SHA-pinned actions as `ci.yml`, and reads one secret, `AGENTOPS_TEST_ANTHROPIC_API_KEY`: a key
from a separate Anthropic test workspace with its own monthly spend limit
(`docs/hosted-config.md`), never the app key. It first fails with a sentence naming the secret
when it is not set (add it under Settings > Secrets and variables > Actions), then installs the
requirements and runs `pytest -q -m live_smoke tests/test_live_smoke.py`, one triage call on the
dev model, and checks that exactly one test passed. `ci.yml` reads no secret and is unchanged by
the live model; in it the live smoke test is skipped and the network guard in
`tests/conftest.py` blocks every non-loopback connection.

The live model added two direct pins, `langchain-anthropic==1.7.5` and `anthropic==1.12.1`, both
MIT. What `anthropic` 1.12.1 pulls in, from `pip show anthropic` on 2026-10-09: anyio,
docstring-parser, httpx2, jiter, pydantic, sniffio, typing-extensions. These float like the other
transitive packages (see Known gaps).

## The test-count floor

A suite that silently skips or collects nothing still exits 0. The floor turns that into a red
build: `scripts/check_test_count.py` counts passed tests (tests minus skipped, failures and
errors) and fails when the count is below `MIN_TESTS`, set in the workflow's `env` block.

To raise it, change `MIN_TESTS` in `.github/workflows/ci.yml` in the same pull request that adds
the tests. Lower it only on purpose, saying why in the commit body.

`MIN_TESTS` is 700 as of increment 4. The rule since increment 3: the largest multiple of 10 at
or below 90 percent of the passing count on the branch, and never below 240.
The suite passed 784 tests (1 skipped) when the floor was set, so 90 percent is 705.6 and the
floor is 700. `tests/test_ci_workflow.py` recomputes the floor from that count. It was 450
as of increment 3 (501 tests passed), and 80 in increment 2, when the suite had 76 tests at
the end of increment 1.

## Re-resolving an action pin

When a tag is moved or a pin needs checking, resolve the tag to its commit SHA with the GitHub
CLI, replacing `<owner>/<repo>` and `<tag>`:

```
gh api repos/<owner>/<repo>/git/ref/tags/<tag>
```

If `object.type` is `commit`, `object.sha` is the pin. If it is `tag` (an annotated tag), call
`gh api repos/<owner>/<repo>/git/tags/<sha>` and use the commit SHA it points at. Dependabot
proposes these updates weekly.

## Accepted advisories

`pip-audit` fails the build on any known advisory. When an advisory has no fixed release, add a
row here and pass `--ignore-vuln <id>` in the workflow for that id only. Never skip the audit as
a whole.

| Advisory id | Package | Why it is accepted | Date | Revisit by |
|---|---|---|---|---|

## Known gaps

Accepted or deferred on purpose, each recorded when increment 1 was built:

- Transitive packages are not locked. Only direct dependencies are exact pins; the packages
  langgraph and langchain-core pull in float at install time, with no lockfile or hashes. Known
  debt, deferred by the design.
- Added 2026-10-25: Flywheel (`langsmith-flywheel`) is not on PyPI, so `requirements.txt` pins
  it to a full GitHub commit. `pip-audit` skips it ("not found on PyPI") and Dependabot
  proposes no upgrade for it; its core uses only the standard library, so nothing else comes
  in with it. Move the commit by hand, together with the Action pin in
  `.github/workflows/flywheel.yml`.
- `pip-audit` covers `requirements.txt` only. The dev tools that run inside CI (pytest, ruff,
  pyright, pip-audit, pyyaml), `langgraph-cli[inmem]` and what it pulls in, and the
  `setuptools` build backend are not audited. Deferred.
- The `pyright` package from PyPI is a wrapper that downloads a Node runtime and the npm pyright
  package on first run (believed, not verified). That is a network fetch of executable code that
  no pin in `requirements-dev.txt` controls. Accepted for now.
- There is no `SECURITY.md` with a disclosure contact. Not needed while the repository is
  private; it arrives before the repository goes public in increment 5.
