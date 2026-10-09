# 0002: Read token usage from the raw reply with `include_raw=True`, and let the node validate the schema itself

Date: 2026-10-05
Status: proposed
Change: 2026-10-05-wire-live-anthropic-model-path

## Context

R6 needs input, output, cache-read and cache-write tokens and the model id for every model
call, written into `state.actions`. `ask_model` (`src/agentops/nodes/__init__.py`) today calls
`with_structured_output(schema, method="json_schema")`, which returns only the parsed object:
the `AIMessage` carrying `usage_metadata` and `response_metadata["model"]` is discarded. The
graph's retry policy retries on `CitationError` and `pydantic.ValidationError` only
(`graph.py`, confirmed). In langchain-anthropic 1.7.5 the `json_schema` method with
`include_raw=True` returns `{"raw", "parsed", "parsing_error"}` and a parse failure is returned
in `parsing_error` as an `OutputParserException`, never raised (confirmed in the wheel source).

## Decision

`ask_model` calls `with_structured_output(schema, method="json_schema", include_raw=True)`.
It takes usage and the model id from `raw`. When `parsed` is a `schema` instance it uses it;
otherwise it calls `schema.model_validate_json(raw.text)`, which raises the
`pydantic.ValidationError` the retry policy already understands. The `ScriptedChatModel`
implements `include_raw=True` the same way and stamps deterministic `usage_metadata` and
`response_metadata={"model": "scripted"}` on every reply, so the accounting path runs in every
test. Cache tokens are read from `usage_metadata["input_token_details"]`: `cache_read`, and
for writes the sum of `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens` when present,
else `cache_creation` (the library zeroes `cache_creation` when the ephemeral keys are set,
confirmed in `_create_usage_metadata`). `input_tokens` already includes cached tokens.

## Alternatives

| Option | Why not |
|--------|---------|
| A `UsageMetadataCallbackHandler` on the invoke config | The scripted model's schema queue bypasses `_generate`, so no callback fires; two accounting paths to keep equal |
| Bind `output_config` on the model directly and parse the text ourselves | Uses a private helper of langchain-anthropic to build the format block; breaks on the next minor release |
| Keep the parsed-only call and estimate tokens from text length | Not a measurement; the eval table would print a guess as a cost |

## Consequences

Easier: one code path records cost for the live model and the fake. Harder: a reply the
library parsed but Pydantic rejects is validated twice (cheap), and the fake must mirror the
`include_raw` contract, which `tests/test_fakes.py` pins. Revisit if `langchain_core` adds a
public structured-output-with-usage API.
