# 0004: Put the cache breakpoint on the stable system block of each node prompt, and cache the baseline's conversation prefix with the library middleware

Date: 2026-10-05
Status: proposed
Change: 2026-10-05-wire-live-anthropic-model-path

## Context

R5 asks for prompt caching on the stable system prompts. Three of the four node system prompts
end with `fence_rule(nonce)`, a sentence that changes on every call (confirmed in `rca.py`,
`postmortem.py`, `escalate.py`), and every human prompt is unique per run. Anthropic caches a
prefix up to a `cache_control` breakpoint, and only when that prefix reaches a model-specific
minimum length (believed from the Anthropic prompt-caching docs: 1024 tokens on Sonnet, 4096 on
Haiku 4.5). The node instruction blocks are about 100 to 200 tokens each. The baseline is a
ReAct loop whose every turn resends the system prompt, the six tool definitions and the growing
history; that prefix is long and stable within a run.

## Decision

`ask_model` sends the system message as two text blocks: the stable instructions plus
`UNTRUSTED_DATA_RULE` first, with `cache_control: {"type": "ephemeral"}`, and the nonce-bearing
fence sentence second. Each node exposes its stable text as a constant so the test can assert
no nonce is inside the cached block. The baseline, when run on the live model, passes
`AnthropicPromptCachingMiddleware(ttl="5m")` from `langchain_anthropic.middleware` to
`create_agent` through a new `middleware` parameter of `run_baseline`; `LiveModels` supplies it
and `ScriptedModels` supplies none, so `baseline.py` imports nothing from the client library.
The spec states plainly that, at today's prompt sizes, the node-level breakpoint is below the
minimum and the API will record no cache write; the `cache_write` and `cache_read` columns in
the eval table are how the owner sees whether caching did anything.

## Alternatives

| Option | Why not |
|--------|---------|
| `model.bind(cache_control=...)` on the node models | langchain-anthropic applies that to the last block of the whole prompt (confirmed, `_apply_cache_control_to_last_eligible_block`): every unique prompt would be written to the cache at 1.25x and read never |
| No caching in this change | The request asks for it and the baseline saving is real; the measured columns make the claim checkable |
| Move the fence nonce out of the system prompt | ADR 0005 of increment 2 put it there so the prompt names its own markers; changing that re-opens the injection tests |

## Consequences

Easier: the baseline's per-run cost drops where it matters. Harder: the system message is a list
of blocks, so one assertion in `tests/test_verify_postmortem.py` that the content is a string
changes to read the blocks' text. Revisit when week-two memory context makes the stable block
long enough to cache.
