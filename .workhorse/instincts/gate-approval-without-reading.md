---
id: gate-approval-without-reading
trigger: "when the owner gives a standing instruction to approve every gate and the packet is not read by a human"
confidence: 0.5
domain: workflow
source: 2026-10-01-increment-3-act-pause-persist-resume
scope: project
---

## Action
Reviewers and the main session must carry the check the human would have made: list open lows and undone items in the approval note. Retro should record which of those the owner has still not seen.

## Evidence
- approvals.md G2, G4, G5: "the owner did not read this brief/packet". G4 notes D25 (AgentShield scan) NOT done, 5 lows open.
