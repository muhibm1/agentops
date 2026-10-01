# 0005: Fence untrusted evidence with a per-prompt nonce and a lookalike-character table

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-3-act-pause-persist-resume

## Context

`neutralise_delimiters` in `src/agentops/nodes/__init__.py` NFKC-normalises strings and spaces
out runs of three ASCII angle brackets. The increment 2 security reviewer confirmed that
lookalikes with no NFKC mapping (U+2039, U+203A, U+3008, U+3009, U+02C2, U+02C3) and a run split
by a zero-width character pass unchanged, and recommended a per-prompt nonce as the stronger
control (R4). Scenario 8 is the standing injection fixture.

## Decision

Two layers. First, before the run regex, every string drops Unicode format characters
(category Cf, which includes zero-width space and joiners) and maps a fixed table of lookalike
brackets (the six above plus U+2329, U+232A, U+27E8, U+27E9, U+300A, U+300B, U+FE64, U+FE65,
U+FF1C, U+FF1E) to ASCII `<` and `>`, so a forged run is spaced out whatever script it uses.
Second, the rca, escalate and postmortem prompts build their fence markers with a random
sixteen-hex-character nonce per prompt (`untrusted evidence <nonce>` and `end <nonce>`), and the
system prompt describes the markers with that nonce, so evidence cannot forge the closing marker
without guessing it. Tests match the markers by pattern, not by literal text.

## Alternatives

| Option | Why not |
|--------|---------|
| Extend only the lookalike table | Any character the table misses is a new finding; the nonce closes the class |
| Only the nonce, no table | Runs of lookalikes would still reach the model as noise that reads like a marker |
| Base64-encode every payload string | Unreadable prompts, and the model's citations of evidence ids become harder to check |

## Consequences

Easier: the forged-marker tests can fail for real. Harder: prompts are no longer byte-identical
between runs, so prompt assertions use regular expressions and LangSmith prompt caching gains
nothing on the fence line. The scripted model is unaffected. Revisit if a live-model experiment
shows the nonce confuses the model.
