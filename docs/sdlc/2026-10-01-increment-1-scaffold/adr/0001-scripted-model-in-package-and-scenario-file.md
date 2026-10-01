# 0001: Ship the scripted chat model in the package and its answers in the scenario file

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-1-scaffold

## Context

Design §16 says increment 1 ends with `agentops run --scenario clean_bad_deploy` reaching RCA
with a fake model, and design §13 places the fake in `tests/fakes.py`. A CLI cannot import from
the test tree once the package is installed without it. The fake also needs the answers a
well-behaved model would give for each scenario, and those answers must cite evidence ids the
scenario declares (R11, R12, R14). The profile's style note names `tests/fakes.py`; this ADR
moves it and the retro should update the note.

## Decision

`ScriptedChatModel` lives in `src/agentops/fakes.py`. It subclasses `BaseChatModel`, returns
scripted JSON text in order, records every prompt, and overrides `with_structured_output` to
validate that text against the schema the node asks for, so a malformed script fails the same
way a malformed live response would. Each scenario file carries a `scripted_outputs` section
with one `Triage` and one `RcaOutput`; `scripted_model_for(scenario)` builds the model from it.
The source never serves that section, exactly as it never serves `ground_truth`.

## Alternatives

| Option | Why not |
|--------|---------|
| Fake in `tests/fakes.py`, CLI adds `tests/` to `sys.path` | Breaks for any install without the test tree and couples the demo to test layout |
| Answers in `tests/fixtures/<id>.json` | A second file per scenario keyed by id; the two drift and the CLI needs a second search path |
| Fake derives answers from `ground_truth` at run time | Hides the citation logic in the fake; tests could no longer script a bad citation on purpose |
| Use `GenericFakeChatModel` from langchain-core | It has no structured-output path (the base implementation needs `bind_tools`, confirmed in the reference) and does not record prompts |

## Consequences

Easier: one code path for the demo and the tests; scripted retries are a list of entries.
Harder: scenario files grow a section that is test data, and anyone adding a scenario must write
its scripted answers too (handover doc, increment 5). The package ships a test double in its
runtime namespace; it imports nothing beyond langchain-core and pydantic, so there is no extra
dependency. Revisit when the live model lands in increment 2: `--model` then has two real values.
