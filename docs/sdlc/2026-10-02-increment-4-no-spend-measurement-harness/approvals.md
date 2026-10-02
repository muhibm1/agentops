# Approvals: increment 4 no-spend measurement harness

Change id: `2026-10-02-increment-4-no-spend-measurement-harness`

Each block is appended by `/workhorse:approve` and committed. Never edit earlier blocks.

<!-- entries -->

## G2: approved

- Who: 90530587+muhibm1@users.noreply.github.com
- When: 2026-10-02T18:44:45.671Z
- Artifact commit: `7fc480f870a5b408327d6a7d26e50c2ab7e73a66` (contains the packet below)
- Packet: `docs/sdlc/2026-10-02-increment-4-no-spend-measurement-harness/brief.md` sha256 `49e264998a3161bb5cabd1dfa6a4e349c5a0ba8416c2a1f09d677a645405cf05`
- Tier at decision: 3
- Notes: D1: recommendation accepted; D2: recommendation accepted; D3: recommendation accepted; D4: recommendation accepted; D5: recommendation accepted; D6: recommendation accepted; D7: recommendation accepted; D8: recommendation accepted; D9: recommendation accepted; D10: recommendation accepted; D11: recommendation accepted; D12: recommendation accepted; D13: recommendation accepted; D14: recommendation accepted; D15: recommendation accepted; D16: recommendation accepted; D17: recommendation accepted; D18: recommendation accepted; D19: recommendation accepted; D20: recommendation accepted. Recorded by the main session on the owner's standing instruction of 2026-10-02 ('you do everything, approve everything ... handle any merges'); the owner did not read this brief. Main-session review: no high audit finding; D17-D20 resolved in design; hard limits (no live model, no LangSmith upload, no .env) stated as D15/D16 and in the request. D13 performed by the main session on the same instruction: two ask_commands entries added to the profile.

## G4: rejected

- Who: 90530587+muhibm1@users.noreply.github.com
- When: 2026-10-02T21:47:12.977Z
- Artifact commit: `eb0f3b96169ca09a50914d6eb0f519d96284f5d0` (contains the packet below)
- Packet: `docs/sdlc/2026-10-02-increment-4-no-spend-measurement-harness/ship.md` sha256 `80e20db9070045a537d14b5a6cb1974a1ad064a831df08dd7e47ed3a1cc592ff`
- Tier at decision: 3
- Notes: Recorded by the main session on the owner's standing instruction of 2026-10-02. Rejected for one finding: bug-reviewer R2b (executor.py:112-130). A recorded approval from thread p1 admits execute on a copied thread p1-x, giving a second apply with no approval on that thread. That breaks the project's two core claims (nothing executes without its own approval; nothing applies twice), whatever the reviewer's severity. Fix: unrecorded_approval_problem must also require record['thread_id'] == the current thread_id (pass it in from execute); confirm CLI forks record their own approval at their own interrupt and keep working; add eval E41 'an approval recorded by another thread does not admit execute' as an adversarial test through the real graph (the reviewer's probe: approve p1, copy state into p1-x via update_state as_node approval with execution=None, invoke; expect refused, no apply record for p1-x, ledger unchanged), plus a negative case that a fork's own approval still executes. Rerun the eval-backed tests at the new head and re-present G4. Everything else in ship.md is accepted as written, including D25's dev-only Elastic-2.0 pins (langgraph-api, langgraph-runtime-inmem, used only by langgraph dev, not redistributed).

## G4: approved

- Who: 90530587+muhibm1@users.noreply.github.com
- When: 2026-10-02T22:00:46.391Z
- Artifact commit: `cdccfa28fe78adc25d8e592c7b29e135af380178` (contains the packet below)
- Packet: `docs/sdlc/2026-10-02-increment-4-no-spend-measurement-harness/ship.md` sha256 `089105af97deb58568e0c31b5d03a53e4c55788feb298fc8fe45a98a03e62fa7`
- Tier at decision: 3
- Notes: D26: recommendation accepted; D25: recommendation accepted; D24: recommendation accepted; D13: recommendation accepted. Recorded by the main session on the owner's standing instruction of 2026-10-02; the owner did not read this packet. Main-session review: R2b fixed in da5a079 (approval bound to thread_id), E49 adversarial through the real graph confirmed failing on the old code, E50 fork golden passes; 878 passed; CI green at f059867 and the later commits are ship.md and conductor-log only (git diff --stat checked). Replaced executor tests verified stronger, not weaker. Open lows accepted as listed.

## G5: approved

- Who: 90530587+muhibm1@users.noreply.github.com
- When: 2026-10-02T22:03:45.548Z
- Artifact commit: `4f6165f96aab1a1bba4390b231a3e576d6d722cf` (contains the packet below)
- Packet: `docs/sdlc/2026-10-02-increment-4-no-spend-measurement-harness/release.md` sha256 `3683b16e2a3a10db7efe323ff632a3960f648b43bef261d3f32eb5fed2e4880e`
- Tier at decision: 3
- Notes: Recorded by the main session on the owner's standing instruction of 2026-10-02. The release was the merge of PR 10 (9e3af26); nothing is deployed; rollback is a revert of the merge commit through a PR.
