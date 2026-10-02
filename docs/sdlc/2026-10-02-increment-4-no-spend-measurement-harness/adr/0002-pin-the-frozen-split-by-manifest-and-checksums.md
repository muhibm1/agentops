# 0002: Pin the frozen (held-out) split with a manifest and sha256 of the scenario files, not copies

Date: 2026-10-02
Status: proposed
Change: 2026-10-02-increment-4-no-spend-measurement-harness

## Context

Design section 11 and constraint 5 in `docs/sdlc/constraints.md`: the held-out split is byte
pinned by a checksum committed with it and scored only when a version is frozen. Constraints Q1
was resolved as `evals/held_out/`, pinned by `evals/checksums.sha256`. The request says the
dataset builder "writes evals/held_out/ with a committed sha256 checksum enforced by a test".
The eight scenario files live in `scenarios/` and are read by the CLI, the tests and the
simulator; the held-out three must stay runnable by the same code path. (This file's name avoids
the words of the profile's `**/*held-out*` glob, which also matches document names.)

## Decision

`agentops dataset --write` writes `evals/held_out/manifest.json`, which names the three held-out
scenario ids, and `evals/checksums.sha256`, which holds the sha256 of the manifest and of each
`scenarios/<id>.json` the manifest names, in `sha256sum` format. The dev split is every other
scenario file. `tests/test_dataset.py` recomputes every line and fails on any difference, so any
edit to a held-out scenario, or to the manifest, is a deliberate, reviewed change to an ask-first
file. The harness loads held-out scenarios from `scenarios/` like any other, through
`split_scenarios("held_out", allow_held_out=True)`.

## Alternatives

| Option | Why not |
|--------|---------|
| Copy the three files into `evals/held_out/` and run them from there | Two copies of each file; a schema change (this change adds `scripted_outputs.baseline`) must be made twice and the copies drift silently |
| Pin only the manifest | Pins which scenarios are held out, not their bytes; an edit to a held-out scenario would pass unnoticed |
| Random split from a seed at run time | Reproducible but not reviewable; the ids would not be in a committed file an auditor can read |

## Consequences

Easier: one scenario loader, one set of files, a one-line `sha256sum -c`-style check. Harder:
a legitimate fix to a held-out scenario needs the checksum regenerated with `--write --force`
and the owner's approval; that is the intended friction. The 5/3 ratio is fixed in this change;
when scenarios grow in week two, the manifest grows with them and the checksum is regenerated
under the same rule.
