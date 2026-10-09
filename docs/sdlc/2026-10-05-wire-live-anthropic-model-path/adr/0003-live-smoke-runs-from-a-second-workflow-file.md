# 0003: Run the live smoke test from a second workflow file, not a second job in `ci.yml`

Date: 2026-10-05
Status: proposed
Change: 2026-10-05-wire-live-anthropic-model-path

## Context

R14 asks for a `workflow_dispatch`-only CI job that reads only `AGENTOPS_TEST_*` secrets. The
keyless promise of the default job is pinned by `tests/test_ci_workflow.py`: it asserts `ci.yml`
has exactly one job and that the text `secrets.` appears nowhere in the file (confirmed). Design
§11 and §13 say the default job has no secrets and live evals run from `workflow_dispatch`.
`.github/workflows/**` is an ask-first path in the profile.

## Decision

Add `.github/workflows/live-smoke.yml`, triggered only by `workflow_dispatch`, with
`permissions: contents: read`, SHA-pinned actions, and three run steps: assert
`AGENTOPS_TEST_ANTHROPIC_API_KEY` is non-empty (exit 1 naming the variable and where to set it),
run `pytest -m live_smoke tests/test_live_smoke.py --junitxml=live.xml`, then
`scripts/check_test_count.py live.xml 1`, which fails when the one test was skipped because the
script counts skipped tests as not passed (confirmed). The file maps only
`secrets.AGENTOPS_TEST_ANTHROPIC_API_KEY` into the environment and never names
`secrets.ANTHROPIC_API_KEY`. `ci.yml` is not edited.

## Alternatives

| Option | Why not |
|--------|---------|
| A second job in `ci.yml` guarded by `if: github.event_name == 'workflow_dispatch'` | The file would contain `secrets.`; the existing test's one-job and no-secrets assertions would have to be weakened, and the keyless proof becomes a conditional instead of a byte-level fact |
| No CI job; the owner runs the smoke test locally only | Design §11 names the dispatch job; without it the smoke test's skip-is-failure rule is never exercised |

## Consequences

Easier: `ci.yml` stays byte-identical, so "the default job reads no secret" is provable by
`git diff`. Harder: two workflow files to keep pinned; Dependabot already covers
`github-actions` weekly. Revisit when live evals (not only the smoke test) move to CI.
