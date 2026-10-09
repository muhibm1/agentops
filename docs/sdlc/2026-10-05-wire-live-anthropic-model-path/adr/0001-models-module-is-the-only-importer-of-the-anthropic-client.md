# 0001: Make `src/agentops/models.py` the only module that imports the Anthropic client, and import it lazily

Date: 2026-10-05
Status: proposed
Change: 2026-10-05-wire-live-anthropic-model-path

## Context

`tests/test_tracing.py` scans every module under `src/agentops/` and fails on any import of
`langsmith`, `langchain_anthropic` or `anthropic` (confirmed). That rule is what proves the
scripted path can never reach a model service. The design (§13) names `models.py` as the home
of `ChatAnthropic` construction. R1, R4 and R15 need one place where the live client is built,
the key is read and the model ids per role are decided, and the rest of the package must keep
depending only on `langchain_core.language_models.BaseChatModel`.

## Decision

`src/agentops/models.py` is the one module allowed to import `langchain_anthropic` and
`anthropic`, and it does so inside the function that builds the live models, not at module
import. It exposes a `ModelFactory` protocol with two implementations: `ScriptedModels` (wraps
`agentops.fakes`) and `LiveModels` (builds one `ChatAnthropic` per role from `ModelSettings`).
The graph, the baseline, the judge and the eval runner receive models through that protocol and
never name the provider. The scan test gains an allowlist holding exactly `models.py` for the two
client roots; `langsmith` stays forbidden everywhere.

## Alternatives

| Option | Why not |
|--------|---------|
| Build `ChatAnthropic` in `cli.py` and `evals/commands.py` where `--model` is parsed | Two places to read the key and set model ids; the scan allowlist would have to name the command modules, which also run the scripted path |
| Import the client at module top in `models.py` | `agentops eval --model scripted` would load the HTTP client library for nothing; a lazy import keeps the scripted path's import graph identical to today |
| Keep the fake model in `fakes.py` and the live one in `models.py` with no shared factory | The eval runner and the CLI would each branch on the model name in three places (graph, baseline, judge) |

## Consequences

Easier: one file to review for key handling and model ids; the import scan stays a one-line
allowlist. Harder: `models.py` must stay free of graph logic, or it becomes a second `cli.py`.
Revisit if a second provider is ever added (design §3 says it is not planned).
