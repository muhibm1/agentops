# Regression gate (Flywheel)

This folder connects AgentOps to [Flywheel](https://github.com/muhibm1/flywheel), which runs a
set of incidents on a pull request and on `main`, compares them case by case, and fails the
check when a case that passed before now fails.

- `dataset.jsonl` holds the cases. Five come from the dev scenarios. Two were mined on
  2026-10-13 from traced Haiku runs in which terse alert wording made the agent escalate a
  two-fault incident instead of fixing it. The held-out scenarios are never used here: the
  adapter refuses them.
- `agentops_flywheel.py` is the adapter: it runs the graph on one case and returns the outcome
  AgentOps's own evaluators grade.
- `traffic.py` sends realistic incidents to a LangSmith project, standing in for production.

## In CI

`.github/workflows/flywheel.yml` runs the gate on every pull request with the scripted model:
free, deterministic, no secret. The scripted model replays a script and never reads a prompt,
so it catches changes to code, not to prompts.

## By hand, on a live model

Prompt and model changes need a live run. From this folder, with the low-limit test key passed
to the command only:

```bash
ANTHROPIC_API_KEY="$AGENTOPS_TEST_ANTHROPIC_API_KEY" AGENTOPS_API_CAP=2 \
  FLYWHEEL_AGENTOPS_MODEL=anthropic \
  flywheel run --backend langsmith --label main --repetitions 3 --out /tmp/main.json
# switch to the branch, then the same with --label head --out /tmp/head.json
flywheel gate --base /tmp/main.json --head /tmp/head.json
```

`AGENTOPS_API_CAP` is the dollar cap from the main README's live-model section. Without it
nothing is sent, and `flywheel run` stops at the first call with one line naming the variable.
Every answer is recorded under `~/.agentops/replay`. The head run replays main's answer wherever
its request is the same as main's, so only the calls the branch changed are paid, and the gate
compares the two on the same answers. A second run of the same commit replays the first, so it
is not a new sample: run it with `AGENTOPS_REPLAY=off` when it should be.

On 2026-10-13 this blocked a shortened root-cause prompt that passed every behavioural unit
test: the two mined cases broke on Sonnet 5.5 (2 of 3 runs correct to 0 of 3, and 3 of 3 to
1 of 3). The report is in Flywheel's
[results page](https://github.com/muhibm1/flywheel/blob/main/docs/results/agentops-2026-10-13.md).

## Growing the dataset

```bash
LANGSMITH_PROJECT=agentops-prod FLYWHEEL_AGENTOPS_MODEL=anthropic FLYWHEEL_AGENTOPS_DEV=1 \
  ANTHROPIC_API_KEY="$AGENTOPS_TEST_ANTHROPIC_API_KEY" AGENTOPS_API_CAP=2 python traffic.py
flywheel score --since 1d          # grade the traces with AgentOps's evaluators
flywheel mine --since 1d --queue agentops-review
flywheel promote .flywheel/candidates.jsonl
```
